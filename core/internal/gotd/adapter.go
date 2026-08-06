package gotd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/gotd/td/session"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/auth"
	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/telegram/message"
	"github.com/gotd/td/telegram/message/styling"
	"github.com/gotd/td/telegram/uploader"
	"github.com/gotd/td/tg"
	"github.com/nats-io/nats.go"
	"go.uber.org/zap"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/config"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/engine"
	"betteragent-core/internal/schema"
)

type GotdAdapter struct {
	client           *telegram.Client
	dispatcher       *tg.UpdateDispatcher
	sender           *message.Sender
	bus              *bus.NatsBus
	stateMachine     *engine.CentralStateMachine
	emotionalState   *emotion.EmotionalState
	personality      *emotion.PersonalityProfile
	circadian        *emotion.CircadianRhythmEvaluator
	typingMgr        *TypingHeartbeatManager
	antiSpam         *AntiSpamGuard
	humanization     *HumanizationEngine
	mediaMgr         *MediaManager
	cfg              *config.Config
	logger           *zap.Logger
	peerMu           sync.RWMutex
	peerAccessHashes map[int64]int64
	peerUsernames    map[int64]string
}

func NewGotdAdapter(
	cfg *config.Config,
	natsBus *bus.NatsBus,
	csm *engine.CentralStateMachine,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	circadian *emotion.CircadianRhythmEvaluator,
	logger *zap.Logger,
) (*GotdAdapter, error) {
	mediaMgr, err := NewMediaManager("./temp", logger)
	if err != nil {
		return nil, err
	}

	dispatcher := tg.NewUpdateDispatcher()

	adapter := &GotdAdapter{
		dispatcher:       &dispatcher,
		cfg:              cfg,
		bus:              natsBus,
		stateMachine:     csm,
		emotionalState:   emoState,
		personality:      personality,
		circadian:        circadian,
		antiSpam:         NewAntiSpamGuard(),
		humanization:     NewHumanizationEngine(),
		mediaMgr:         mediaMgr,
		logger:           logger,
		peerAccessHashes: make(map[int64]int64),
		peerUsernames:    make(map[int64]string),
	}

	adapter.typingMgr = NewTypingHeartbeatManager(adapter, logger)

	csm.SetTimeoutCallback(func(chatID int64, state engine.State) {
		logger.Warn("State machine watchdog callback triggered: stopping typing heartbeat", zap.Int64("chat_id", chatID), zap.String("stuck_state", string(state)))
		adapter.typingMgr.StopHeartbeat(chatID)
	})

	sessionPath := "gotd.session.json"
	if _, err := os.Stat(sessionPath); os.IsNotExist(err) {
		if _, errCore := os.Stat("core/gotd.session.json"); errCore == nil {
			sessionPath = "core/gotd.session.json"
		}
	}
	sessionStorage := &session.FileStorage{Path: sessionPath}

	client := telegram.NewClient(cfg.TelegramAPIID, cfg.TelegramAPIHash, telegram.Options{
		Logger:         logger,
		SessionStorage: sessionStorage,
		UpdateHandler:  dispatcher,
	})
	adapter.client = client

	return adapter, nil
}

func (a *GotdAdapter) SendTypingAction(ctx context.Context, chatID int64, action string) error {
	raw := a.client.API()
	var act tg.SendMessageActionClass

	switch action {
	case "record_audio":
		act = &tg.SendMessageRecordAudioAction{}
	default:
		act = &tg.SendMessageTypingAction{}
	}

	peer := &tg.InputPeerUser{UserID: chatID}
	_, err := raw.MessagesSetTyping(ctx, &tg.MessagesSetTypingRequest{
		Peer:   peer,
		Action: act,
	})
	return err
}

func (a *GotdAdapter) Start(ctx context.Context) error {
	a.logger.Info("Starting GotdAdapter (User Account)...")

	// Register dispatcher event handlers
	a.dispatcher.OnNewMessage(a.handleIncomingMessage)

	// Subscribe to ActionDecision from NATS
	_, err := a.bus.Subscribe(bus.SubjectActionDecision, a.handleActionDecision)
	if err != nil {
		return fmt.Errorf("failed to subscribe to %s: %w", bus.SubjectActionDecision, err)
	}

	gapi := tg.NewClient(a.client)
	a.sender = message.NewSender(gapi)

	return a.client.Run(ctx, func(ctx context.Context) error {
		// Terminal interactive flow for authentication fallback
		flow := auth.NewFlow(
			auth.Constant(a.cfg.TelegramPhone, "", auth.CodeAuthenticatorFunc(func(ctx context.Context, sentCode *tg.AuthSentCode) (string, error) {
				fmt.Print("Enter Telegram SMS verification code: ")
				var code string
				_, err := fmt.Scanln(&code)
				return code, err
			})),
			auth.SendCodeOptions{},
		)

		if err := a.client.Auth().IfNecessary(ctx, flow); err != nil {
			return fmt.Errorf("authentication error: %w", err)
		}

		user, err := a.client.Self(ctx)
		if err != nil {
			return fmt.Errorf("failed to get self info: %w", err)
		}

		a.logger.Info("GotdAdapter Authorized successfully & Session persisted to gotd.session.json!",
			zap.String("username", user.Username),
			zap.Int64("id", user.ID),
		)

		// Send active online report message on startup initialization & pre-cache active dialog peers
		go func() {
			time.Sleep(1 * time.Second)
			statusText := "🐱 **BetterAgent 猫娘健康度指标**\n\n" +
				"• **系统状态**: 正常在线 🟢\n" +
				"• **触发模式**: initialization\n" +
				"• **短期记忆缓冲**: 0 条\n" +
				"• **RAG 检索事实数**: 0 条"

			target := a.sender.Self()
			if _, sErr := target.Text(ctx, statusText); sErr != nil {
				a.logger.Debug("Startup online notification error", zap.Error(sErr))
			} else {
				a.logger.Info("Sent active startup online notification card to Telegram Saved Messages")
			}

			// Pre-populate peer AccessHash cache from active dialogs
			dialogs, dErr := gapi.MessagesGetDialogs(ctx, &tg.MessagesGetDialogsRequest{
				OffsetPeer: &tg.InputPeerEmpty{},
				Limit:      100,
			})
			if dErr == nil {
				a.peerMu.Lock()
				switch d := dialogs.(type) {
				case *tg.MessagesDialogs:
					for _, u := range d.Users {
						if user, ok := u.AsNotEmpty(); ok && user.AccessHash != 0 {
							a.peerAccessHashes[user.ID] = user.AccessHash
							if user.Username != "" {
								a.peerUsernames[user.ID] = user.Username
							}
						}
					}
					for _, ch := range d.Chats {
						if channel, ok := ch.(*tg.Channel); ok && channel.AccessHash != 0 {
							a.peerAccessHashes[-1000000000000-channel.ID] = channel.AccessHash
						}
					}
				case *tg.MessagesDialogsSlice:
					for _, u := range d.Users {
						if user, ok := u.AsNotEmpty(); ok && user.AccessHash != 0 {
							a.peerAccessHashes[user.ID] = user.AccessHash
							if user.Username != "" {
								a.peerUsernames[user.ID] = user.Username
							}
						}
					}
					for _, ch := range d.Chats {
						if channel, ok := ch.(*tg.Channel); ok && channel.AccessHash != 0 {
							a.peerAccessHashes[-1000000000000-channel.ID] = channel.AccessHash
						}
					}
				}
				a.peerMu.Unlock()
				a.logger.Info("Pre-populated Telegram peer AccessHash cache from dialogs", zap.Int("cached_peers", len(a.peerAccessHashes)))
			}
		}()

		<-ctx.Done()
		return ctx.Err()
	})
}

func (a *GotdAdapter) handleIncomingMessage(ctx context.Context, e tg.Entities, update *tg.UpdateNewMessage) error {
	msg, ok := update.Message.(*tg.Message)
	if !ok || msg.Out { // Ignore outgoing messages
		return nil
	}

	// Filter: private messages only for now
	peerUser, isPrivate := msg.PeerID.(*tg.PeerUser)
	if !isPrivate {
		a.logger.Debug("Ignoring non-private message")
		return nil
	}

	userID := peerUser.UserID
	chatID := userID

	a.logger.Info("Incoming message received",
		zap.Int64("chat_id", chatID),
		zap.String("text", msg.Message),
	)

	// Update emotion state with basic positive/negative delta heuristic
	dV, dA, dAff := a.personality.ModifySentimentDelta(0.05, 0.05, 0.5)
	a.emotionalState.ApplySentimentDelta(dV, dA, dAff)

	// Cache AccessHash and Username from entities for MTProto input peer resolution
	a.peerMu.Lock()
	if senderUser, found := e.Users[userID]; found && senderUser != nil {
		if senderUser.AccessHash != 0 {
			a.peerAccessHashes[userID] = senderUser.AccessHash
		}
		if senderUser.Username != "" {
			a.peerUsernames[userID] = senderUser.Username
		}
	}
	for chID, ch := range e.Channels {
		if ch != nil && ch.AccessHash != 0 {
			a.peerAccessHashes[-1000000000000-chID] = ch.AccessHash
		}
	}
	a.peerMu.Unlock()

	// Build InboundMessagePayload
	rawText := msg.Message
	senderUser, found := e.Users[userID]
	senderFirstName := "User"
	var senderUsername *string
	var senderLastName *string

	if found {
		senderFirstName = senderUser.FirstName
		if senderUser.Username != "" {
			uStr := senderUser.Username
			senderUsername = &uStr
		}
		if senderUser.LastName != "" {
			lStr := senderUser.LastName
			senderLastName = &lStr
		}
	}

	senderDisplayName := senderFirstName
	if senderLastName != nil {
		senderDisplayName = fmt.Sprintf("%s %s", senderFirstName, *senderLastName)
	}

	// Check for user incoming media (photos) and download to temp
	var inboundFilePath *string
	var inboundMediaType *string

	if msg.Media != nil {
		if mediaPhoto, isPhoto := msg.Media.(*tg.MessageMediaPhoto); isPhoto {
			if photo, isP := mediaPhoto.Photo.(*tg.Photo); isP {
				_ = os.MkdirAll("temp", 0755)
				savePath := filepath.Join("temp", fmt.Sprintf("inbound_photo_%d_%d.jpg", msg.ID, time.Now().Unix()))
				d := downloader.NewDownloader()
				location := &tg.InputPhotoFileLocation{
					ID:            photo.ID,
					AccessHash:    photo.AccessHash,
					FileReference: photo.FileReference,
					ThumbSize:     "y",
				}
				_, err := d.Download(a.client.API(), location).ToPath(ctx, savePath)
				if err == nil {
					a.logger.Info("Downloaded user incoming photo", zap.String("path", savePath))
					inboundFilePath = &savePath
					mType := "photo"
					inboundMediaType = &mType
				} else {
					a.logger.Warn("Failed to download user incoming photo", zap.Error(err))
				}
			}
		}
	}

	inboundPayload := schema.InboundMessagePayload{
		BasePayload:       schema.NewBasePayload("gotd_adapter"),
		ChatID:            chatID,
		UserID:            userID,
		MessageID:         msg.ID,
		RawText:           &rawText,
		FilePath:          inboundFilePath,
		MediaType:         inboundMediaType,
		ChatType:          "private",
		SenderFirstName:   senderFirstName,
		SenderLastName:    senderLastName,
		SenderDisplayName: senderDisplayName,
		SenderUsername:    senderUsername,
	}

	// Publish InboundMessage to NATS
	_ = a.bus.Publish(bus.SubjectInboundMessage, "gotd_adapter", inboundPayload)

	// Central State Machine transition to THINKING for this chatID
	a.stateMachine.TransitionToChat(chatID, engine.StateThinking, "inbound_message")

	// Start Typing Heartbeat
	a.typingMgr.StartHeartbeat(chatID, "typing")

	// Build EnrichContextReqPayload
	enrichReq := schema.EnrichContextReqPayload{
		BasePayload:            schema.NewBasePayload("gotd_adapter"),
		ChatID:                 chatID,
		UserID:                 userID,
		InboundMessage:         &inboundPayload,
		CurrentState:           string(a.stateMachine.GetChatState(chatID)),
		TriggerType:            "user_message",
		EmotionDescription:     a.emotionalState.ToPromptDescription(),
		PersonalityDescription: a.personality.ToPromptDescription(),
		CircadianDescription:   a.circadian.ToPromptDescription(),
	}

	// Request Enrich Context synchronously via NATS (5s timeout)
	reasoningReq, err := a.bus.Request(bus.SubjectEnrichContextReq, "gotd_adapter", enrichReq, 5*time.Second)
	if err != nil {
		a.logger.Warn("EnrichContext request timeout/failed, continuing via async pub", zap.Error(err))
		// Publish async fallback request
		_ = a.bus.Publish(bus.SubjectEnrichContextReq, "gotd_adapter", enrichReq)
		return nil
	}

	// Defensive ChatID preservation check
	if reasoningReq.ChatID == 0 {
		reasoningReq.ChatID = chatID
	}
	if reasoningReq.UserID == 0 {
		reasoningReq.UserID = userID
	}

	// Publish Reasoning Request
	return a.bus.Publish(bus.SubjectReasoningRequest, "gotd_adapter", reasoningReq)
}

func (a *GotdAdapter) handleActionDecision(msg *nats.Msg) {
	var env struct {
		ID        string                       `json:"id"`
		Subject   string                       `json:"subject"`
		Timestamp float64                      `json:"timestamp"`
		Source    string                       `json:"source"`
		Payload   schema.ActionDecisionPayload `json:"payload"`
	}

	var action schema.ActionDecisionPayload
	if err := json.Unmarshal(msg.Data, &env); err == nil && env.Payload.ChatID != 0 {
		action = env.Payload
	} else if errDirect := json.Unmarshal(msg.Data, &action); errDirect != nil {
		a.logger.Error("Failed to unmarshal ActionDecisionPayload", zap.Error(errDirect))
		return
	}

	a.logger.Info("ActionDecision received",
		zap.Int64("chat_id", action.ChatID),
		zap.String("action_type", action.ActionType),
	)

	// Explicit Channel Routing: Ignore action decisions intended for web or other non-telegram channels
	if action.SourceChannel != "" && action.SourceChannel != "telegram" {
		a.logger.Debug("Ignoring ActionDecision non-telegram channel in GotdAdapter", zap.String("source_channel", action.SourceChannel), zap.Int64("chat_id", action.ChatID))
		return
	}

	// Stop typing heartbeat
	a.typingMgr.StopHeartbeat(action.ChatID)

	// Calculate humanization delay
	text := ""
	if action.TextContent != nil {
		text = *action.TextContent
	}
	delay := a.humanization.CalculateDelay(text, action.MediaType)
	time.Sleep(delay)

	// Anti-Spam wait
	ctx := context.Background()
	if err := a.antiSpam.Wait(ctx, action.ChatID); err != nil {
		a.logger.Error("AntiSpam wait error", zap.Error(err))
	}

	// Execute Send Action via Sender
	var sentMsgID int = 0
	status := "success"
	var errDetail *string

	switch action.ActionType {
	case "send_photo":
		// Attempt to upload the local photo file to Telegram
		photoSent := false
		if action.PhotoPath != nil && *action.PhotoPath != "" {
			photoPath := *action.PhotoPath
			f, ferr := os.Open(photoPath)
			if ferr != nil {
				// Try alternate path relative to temp directory
				altPath := filepath.Join("temp", filepath.Base(photoPath))
				if fAlt, errAlt := os.Open(altPath); errAlt == nil {
					f = fAlt
					ferr = nil
				} else if fParent, errParent := os.Open(filepath.Join("..", "temp", filepath.Base(photoPath))); errParent == nil {
					f = fParent
					ferr = nil
				}
			}
			if ferr == nil {
				defer f.Close()
				data, rerr := io.ReadAll(f)
				if rerr == nil {
					u := uploader.NewUploader(a.client.API())
					senderWithUploader := a.sender.WithUploader(u)
					upFile, uerr := u.FromBytes(ctx, "photo.jpg", data)
					if uerr == nil {
						caption := ""
						if action.TextContent != nil {
							caption = *action.TextContent
						}
						inputPeer, perr := a.resolveInputPeer(ctx, action.ChatID)
						if perr == nil {
							target := senderWithUploader.To(inputPeer)
							_, serr := target.UploadedPhoto(ctx, upFile, styling.Plain(caption))
							if serr != nil {
								a.logger.Warn("Failed to send photo, falling back to text", zap.Error(serr))
							} else {
								a.logger.Info("Sent photo to Telegram", zap.Int64("chat_id", action.ChatID))
								photoSent = true
							}
						} else {
							a.logger.Warn("Failed to resolve input peer for photo", zap.Int64("chat_id", action.ChatID), zap.Error(perr))
						}
					}
				}
			} else {
				a.logger.Warn("Photo file not found", zap.String("path", *action.PhotoPath), zap.Error(ferr))
			}
		}
		// Fall back to sending text caption if photo failed
		if !photoSent && action.TextContent != nil && *action.TextContent != "" {
			inputPeer, perr := a.resolveInputPeer(ctx, action.ChatID)
			if perr == nil {
				target := a.sender.To(inputPeer)
				_, err := target.Text(ctx, *action.TextContent)
				if err != nil {
					status = "failed"
					errStr := err.Error()
					errDetail = &errStr
					a.logger.Error("Failed to send fallback text for photo", zap.Error(err))
				}
			} else {
				status = "failed"
				a.logger.Error("Failed to resolve peer for photo fallback text", zap.Int64("chat_id", action.ChatID), zap.Error(perr))
			}
		}

	case "send_sticker", "sticker":
		if action.StickerID != nil && *action.StickerID != "" {
			inputPeer, perr := a.resolveInputPeer(ctx, action.ChatID)
			if perr == nil {
				stickerID := *action.StickerID
				stickerSent := false

				// Check if StickerID is a local webp/png/tgs sticker file
				f, ferr := os.Open(stickerID)
				if ferr != nil {
					altPath := filepath.Join("temp", filepath.Base(stickerID))
					if fAlt, errAlt := os.Open(altPath); errAlt == nil {
						f = fAlt
						ferr = nil
					}
				}

				if ferr == nil {
					defer f.Close()
					data, rerr := io.ReadAll(f)
					if rerr == nil {
						u := uploader.NewUploader(a.client.API())
						upFile, uerr := u.FromBytes(ctx, "sticker.webp", data)
						if uerr == nil {
							_, serr := a.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
								Peer: inputPeer,
								Media: &tg.InputMediaUploadedDocument{
									File:     upFile,
									MimeType: "image/webp",
									Attributes: []tg.DocumentAttributeClass{
										&tg.DocumentAttributeSticker{
											Alt:        "🐱",
											Stickerset: &tg.InputStickerSetEmpty{},
										},
									},
								},
								RandomID: time.Now().UnixNano(),
							})
							if serr == nil {
								a.logger.Info("Sent uploaded custom sticker to Telegram", zap.Int64("chat_id", action.ChatID))
								stickerSent = true
							} else {
								a.logger.Warn("Failed to send uploaded sticker media", zap.Error(serr))
							}
						}
					}
				}

				if !stickerSent {
					a.logger.Info("Sticker action processed for Telegram", zap.Int64("chat_id", action.ChatID), zap.String("sticker_id", stickerID))
				}
			} else {
				status = "failed"
				a.logger.Error("Failed to resolve peer for sticker action", zap.Int64("chat_id", action.ChatID), zap.Error(perr))
			}
		}

	default:
		// send_message and all other action types
		if action.TextContent != nil && *action.TextContent != "" {
			inputPeer, perr := a.resolveInputPeer(ctx, action.ChatID)
			if perr == nil {
				target := a.sender.To(inputPeer)
				_, err := target.Text(ctx, *action.TextContent)
				if err != nil {
					status = "failed"
					errStr := err.Error()
					errDetail = &errStr
					a.logger.Error("Failed to send message to Telegram", zap.Int64("chat_id", action.ChatID), zap.Error(err))
				} else {
					a.logger.Info("Sent message to Telegram", zap.Int64("chat_id", action.ChatID), zap.String("text", *action.TextContent))
				}
			} else {
				status = "failed"
				a.logger.Error("Failed to resolve input peer for send_message", zap.Int64("chat_id", action.ChatID), zap.Error(perr))
			}
		}
	}

	// Transition per-chat state machine back to IDLE
	a.stateMachine.TransitionToChat(action.ChatID, engine.StateIdle, "action_completed")

	// Publish ActionCompleted to NATS
	completedPayload := schema.ActionCompletedPayload{
		BasePayload:    schema.NewBasePayload("gotd_adapter"),
		ChatID:         action.ChatID,
		SentMessageID:  &sentMsgID,
		ActionDecision: action,
		Status:         status,
		SentTime:       float64(time.Now().UnixNano()) / 1e9,
		ErrorDetail:    errDetail,
	}

	if err := a.bus.Publish(bus.SubjectActionCompleted, "gotd_adapter", completedPayload); err != nil {
		a.logger.Error("Failed to publish ActionCompleted to NATS bus", zap.Int64("chat_id", action.ChatID), zap.Error(err))
	}
}

func (a *GotdAdapter) resolveInputPeer(ctx context.Context, chatID int64) (tg.InputPeerClass, error) {
	if chatID > 0 {
		a.peerMu.RLock()
		accessHash, hasHash := a.peerAccessHashes[chatID]
		username, hasUsername := a.peerUsernames[chatID]
		a.peerMu.RUnlock()

		if hasHash && accessHash != 0 {
			return &tg.InputPeerUser{UserID: chatID, AccessHash: accessHash}, nil
		}

		if hasUsername && username != "" {
			p, err := a.sender.Resolve(username).AsInputPeer(ctx)
			if err == nil {
				return p, nil
			}
		}

		// On-demand fetch user info via UsersGetUsers API if not in cache
		gapi := tg.NewClient(a.client)
		users, uerr := gapi.UsersGetUsers(ctx, []tg.InputUserClass{&tg.InputUser{UserID: chatID}})
		if uerr == nil && len(users) > 0 {
			if u, ok := users[0].AsNotEmpty(); ok && u.AccessHash != 0 {
				a.peerMu.Lock()
				a.peerAccessHashes[chatID] = u.AccessHash
				if u.Username != "" {
					a.peerUsernames[chatID] = u.Username
				}
				a.peerMu.Unlock()
				a.logger.Info("Fetched MTProto user AccessHash on demand via UsersGetUsers", zap.Int64("user_id", chatID))
				return &tg.InputPeerUser{UserID: chatID, AccessHash: u.AccessHash}, nil
			}
		}

		return &tg.InputPeerUser{UserID: chatID}, nil
	}

	absID := -chatID
	// Check if Channel or Supergroup (-100... prefix, e.g. -1001234567890)
	if absID > 1000000000000 {
		channelID := absID - 1000000000000
		a.peerMu.RLock()
		accessHash, ok := a.peerAccessHashes[chatID]
		a.peerMu.RUnlock()
		if ok && accessHash != 0 {
			return &tg.InputPeerChannel{ChannelID: channelID, AccessHash: accessHash}, nil
		}
		return &tg.InputPeerChannel{ChannelID: channelID}, nil
	}

	// Basic Group Chat
	return &tg.InputPeerChat{ChatID: absID}, nil
}

package gotd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
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
	"betteragent-core/internal/idspace"
	"betteragent-core/internal/schema"
)

type GotdAdapter struct {
	client              *telegram.Client
	dispatcher          *tg.UpdateDispatcher
	sender              *message.Sender
	bus                 *bus.NatsBus
	stateMachine        *engine.CentralStateMachine
	emotionalState      *emotion.EmotionalState
	personality         *emotion.PersonalityProfile
	circadian           *emotion.CircadianRhythmEvaluator
	urgeEngine          *engine.UrgeEngine
	autonomousPlayState *engine.AutonomousPlayState
	typingMgr           *TypingHeartbeatManager
	antiSpam            *AntiSpamGuard
	humanization        *HumanizationEngine
	mediaMgr            *MediaManager
	cfg                 *config.Config
	logger              *zap.Logger
	peerMu              sync.RWMutex
	peerAccessHashes    map[int64]int64
	peerUsernames       map[int64]string
	peerLastSeen        map[int64]time.Time
	peerCachePruned     time.Time
	textBuffer          map[int64][]string
}

// peerCacheIdleTTL/peerCachePruneEvery bound the growth of the AccessHash/
// Username cache. It's a legitimate long-lived cache (avoids re-hitting
// Telegram's API, which has its own flood-wait limits) bounded by real
// MTProto interactions, so the TTL is generous -- this is defense in depth
// against unbounded growth over a long-running process, not a hot path fix.
const (
	peerCacheIdleTTL    = 30 * 24 * time.Hour
	peerCachePruneEvery = 24 * time.Hour
)

func NewGotdAdapter(
	cfg *config.Config,
	natsBus *bus.NatsBus,
	csm *engine.CentralStateMachine,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	circadian *emotion.CircadianRhythmEvaluator,
	urgeEngine *engine.UrgeEngine,
	autonomousPlayState *engine.AutonomousPlayState,
	logger *zap.Logger,
) (*GotdAdapter, error) {
	mediaMgr, err := NewMediaManager("./temp", logger)
	if err != nil {
		return nil, err
	}

	dispatcher := tg.NewUpdateDispatcher()

	adapter := &GotdAdapter{
		dispatcher:          &dispatcher,
		cfg:                 cfg,
		bus:                 natsBus,
		stateMachine:        csm,
		emotionalState:      emoState,
		personality:         personality,
		circadian:           circadian,
		urgeEngine:          urgeEngine,
		autonomousPlayState: autonomousPlayState,
		antiSpam:            NewAntiSpamGuard(),
		humanization:        NewHumanizationEngine(),
		mediaMgr:            mediaMgr,
		logger:              logger,
		peerAccessHashes:    make(map[int64]int64),
		peerUsernames:       make(map[int64]string),
		peerLastSeen:        make(map[int64]time.Time),
		textBuffer:          make(map[int64][]string),
	}

	adapter.typingMgr = NewTypingHeartbeatManager(adapter, adapter.antiSpam, logger)

	csm.SetTimeoutCallback(func(chatID int64, state engine.State) {
		logger.Warn("State machine watchdog callback triggered: stopping typing heartbeat", zap.Int64("chat_id", chatID), zap.String("stuck_state", string(state)))
		adapter.typingMgr.StopHeartbeat(chatID)
		// Also drop any partially-buffered stream text: if IsFinal never
		// arrives (crashed/buggy upstream), textBuffer would otherwise grow
		// unbounded for that chat.
		adapter.peerMu.Lock()
		delete(adapter.textBuffer, chatID)
		adapter.peerMu.Unlock()
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

	// Subscribe to ActionDecision from NATS -- only the "telegram" channel's
	// subjects, so web-bound decisions are never even delivered here (see
	// bus.ActionDecisionWildcard).
	telegramActionSubject := bus.ActionDecisionWildcard("telegram")
	_, err := a.bus.Subscribe(telegramActionSubject, a.handleActionDecision)
	if err != nil {
		return fmt.Errorf("failed to subscribe to %s: %w", telegramActionSubject, err)
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
							a.peerLastSeen[user.ID] = time.Now()
							if user.Username != "" {
								a.peerUsernames[user.ID] = user.Username
							}
						}
					}
					for _, ch := range d.Chats {
						if channel, ok := ch.(*tg.Channel); ok && channel.AccessHash != 0 {
							a.peerAccessHashes[-1000000000000-channel.ID] = channel.AccessHash
							a.peerLastSeen[-1000000000000-channel.ID] = time.Now()
						}
					}
				case *tg.MessagesDialogsSlice:
					for _, u := range d.Users {
						if user, ok := u.AsNotEmpty(); ok && user.AccessHash != 0 {
							a.peerAccessHashes[user.ID] = user.AccessHash
							a.peerLastSeen[user.ID] = time.Now()
							if user.Username != "" {
								a.peerUsernames[user.ID] = user.Username
							}
						}
					}
					for _, ch := range d.Chats {
						if channel, ok := ch.(*tg.Channel); ok && channel.AccessHash != 0 {
							a.peerAccessHashes[-1000000000000-channel.ID] = channel.AccessHash
							a.peerLastSeen[-1000000000000-channel.ID] = time.Now()
						}
					}
				}
				a.pruneStalePeersLocked()
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
			a.peerLastSeen[userID] = time.Now()
		}
		if senderUser.Username != "" {
			a.peerUsernames[userID] = senderUser.Username
		}
	}
	for chID, ch := range e.Channels {
		if ch != nil && ch.AccessHash != 0 {
			a.peerAccessHashes[-1000000000000-chID] = ch.AccessHash
			a.peerLastSeen[-1000000000000-chID] = time.Now()
		}
	}
	a.pruneStalePeersLocked()
	a.peerMu.Unlock()

	// Build InboundMessagePayload
	rawText := msg.Message

	// /game_start and /game_stop are intercepted here, before anything else
	// touches NATS/CSM/the LLM -- see handleGameStartStopCommand's doc comment.
	if a.handleGameStartStopCommand(ctx, chatID, rawText) {
		return nil
	}

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
	if err := a.bus.Publish(bus.SubjectInboundMessage, "gotd_adapter", inboundPayload); err != nil {
		a.logger.Error("Failed to publish InboundMessage to NATS", zap.Int64("chat_id", chatID), zap.Error(err))
	}

	// Central State Machine transition to THINKING for this chatID
	a.stateMachine.TransitionToChat(chatID, engine.StateThinking, "inbound_message")
	a.stateMachine.TouchActivity(chatID)

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

	reasoningReq, err := a.bus.Request(bus.SubjectEnrichContextReq, "gotd_adapter", enrichReq, 5*time.Second)
	if err != nil {
		a.logger.Warn("GotdAdapter EnrichContext timeout/fallback to direct ReasoningRequest publish", zap.Error(err))
		triggerType := "user_message"
		fallbackReasoning := schema.ReasoningRequestPayload{
			BasePayload:    schema.NewBasePayload("gotd_adapter"),
			ChatID:         chatID,
			UserID:         userID,
			GenerationID:   a.stateMachine.GetGenerationChat(chatID),
			InboundMessage: &inboundPayload,
			CurrentEmotion: a.emotionalState.ToPromptDescription(),
			TriggerType:    &triggerType,
			SourceChannel:  "telegram",
		}
		if pubErr := a.bus.Publish(bus.SubjectReasoningRequest, "gotd_adapter", fallbackReasoning); pubErr != nil {
			a.logger.Error("Failed to publish fallback ReasoningRequest to NATS", zap.Int64("chat_id", chatID), zap.Error(pubErr))
			if a.stateMachine != nil {
				a.stateMachine.TransitionToChat(chatID, engine.StateIdle, "enrich_context_failed")
			}
		}
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

// handleGameStartStopCommand intercepts "/game_start" and "/game_stop" as
// raw literal text, before anything else in handleIncomingMessage runs --
// they never reach NATS/CSM/the LLM at all. Returns true if the text was
// one of these commands.
//
// /game_stop is the actual emergency stop for autonomous play: flipping
// AutonomousPlayState off only prevents *future* game turns from firing --
// it does nothing about a turn already in flight. Publishing
// SubjectStreamCancelReq/SubjectUserInterrupt additionally reaches
// cognitive_service's existing cancel_chat_stream and tears down any
// in-progress tool-calling round. This is deliberately deterministic and
// Go-side: it works even if the LLM is currently misbehaving, because it
// never depends on the LLM cooperating.
func (a *GotdAdapter) handleGameStartStopCommand(ctx context.Context, chatID int64, text string) bool {
	if a.autonomousPlayState == nil {
		return false
	}

	cmd := strings.TrimSpace(text)
	if idx := strings.LastIndex(cmd, "/game_"); idx != -1 {
		cmd = cmd[idx:]
	}
	cmd = strings.TrimSpace(cmd)

	if strings.HasPrefix(cmd, "/game_start") {
		a.autonomousPlayState.Activate(chatID)
		a.logger.Info("🎮 Autonomous play activated", zap.Int64("chat_id", chatID))
		a.replyDirect(ctx, chatID, "游戏自动托管已开启喵～ 发送 /game_stop 可以随时叫停我。")
		return true
	}
	if strings.HasPrefix(cmd, "/game_stop") {
		deactivatedChatID := a.autonomousPlayState.Deactivate()
		a.logger.Info("🛑 Autonomous play deactivated", zap.Int64("chat_id", chatID))
		if deactivatedChatID != 0 {
			cancelPayload := schema.StreamCancelPayload{
				BasePayload:   schema.NewBasePayload("gotd_adapter"),
				ChatID:        deactivatedChatID,
				GenerationID:  a.stateMachine.GetGenerationChat(deactivatedChatID),
				Reason:        "game_stop_emergency_cancel",
				SourceChannel: "telegram",
			}
			if err := a.bus.Publish(bus.SubjectStreamCancelReq, "gotd_adapter", cancelPayload); err != nil {
				a.logger.Error("Failed to publish StreamCancelReq on /game_stop", zap.Int64("chat_id", deactivatedChatID), zap.Error(err))
			}
			if err := a.bus.Publish(bus.SubjectUserInterrupt, "gotd_adapter", cancelPayload); err != nil {
				a.logger.Error("Failed to publish UserInterrupt on /game_stop", zap.Int64("chat_id", deactivatedChatID), zap.Error(err))
			}
		}
		a.replyDirect(ctx, chatID, "游戏自动托管已停止，操作权还给主人啦。")
		return true
	}
	return false
}

// replyDirect sends a Telegram text reply straight through the sender,
// bypassing NATS/LLM entirely -- used for the /game_start /game_stop
// confirmations, which must work even if the reasoning pipeline is stuck.
func (a *GotdAdapter) replyDirect(ctx context.Context, chatID int64, text string) {
	if a.sender == nil {
		a.logger.Warn("Skipped game start/stop reply: sender not initialized yet", zap.Int64("chat_id", chatID))
		return
	}
	inputPeer, err := a.resolveInputPeer(ctx, chatID)
	if err != nil {
		a.logger.Error("Failed to resolve input peer for game start/stop reply", zap.Int64("chat_id", chatID), zap.Error(err))
		return
	}
	if _, err := a.sender.To(inputPeer).Text(ctx, text); err != nil {
		a.logger.Error("Failed to send game start/stop reply to Telegram", zap.Int64("chat_id", chatID), zap.Error(err))
	}
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

	// Channel routing is now handled by NATS itself (subscribed only to
	// agent.action.telegram.* -- see Start). SourceChannel is a
	// self-reported payload field; downgrade a mismatch to a log rather than
	// a drop, since the subject already proved this message belongs here.
	//
	// IsWebChat stays a hard `return`, deliberately NOT folded into the
	// log-only check above: it's the specific defense-in-depth guard that
	// caught a real PEER_ID_INVALID incident (a web-namespaced chat_id
	// reaching Telegram's send path), and it's a structurally independent,
	// cheap, numeric-range invariant -- unlike SourceChannel, trusting it
	// isn't what this refactor is changing. Keep it even though the
	// wildcard subscription should make it unreachable in practice.
	if action.SourceChannel != "" && action.SourceChannel != "telegram" {
		a.logger.Error("received ActionDecision on telegram-channel subject with mismatched source_channel payload field -- processing anyway, subject is authoritative post-refactor",
			zap.String("source_channel", action.SourceChannel), zap.Int64("chat_id", action.ChatID))
	}
	if idspace.IsWebChat(action.ChatID) {
		a.logger.Error("refusing telegram send for a WebGateway-namespaced chat_id -- should be structurally impossible after subject-graded routing, treating as a hard safety violation",
			zap.Int64("chat_id", action.ChatID))
		return
	}

	// Stop typing heartbeat ONLY when IsFinal == true (end of streaming)
	if action.IsFinal {
		a.typingMgr.StopHeartbeat(action.ChatID)
	}

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
			// PhotoPath ultimately comes from LLM output via NATS -- treat as
			// untrusted and resolve strictly within the managed temp dir.
			// See docs/SECURITY.md §2.7.
			resolvedPath, presolveErr := a.mediaMgr.ResolveMediaPath(*action.PhotoPath)
			if presolveErr != nil {
				a.logger.Warn("Rejected photo path outside managed temp dir", zap.String("reported_path", *action.PhotoPath), zap.Error(presolveErr))
			} else if f, ferr := os.Open(resolvedPath); ferr != nil {
				a.logger.Warn("Photo file not found", zap.String("path", resolvedPath), zap.Error(ferr))
			} else {
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
				stickerSent := false

				// StickerID ultimately comes from LLM output via NATS -- treat
				// as untrusted and resolve strictly within the managed temp
				// dir. See docs/SECURITY.md §2.7.
				resolvedPath, presolveErr := a.mediaMgr.ResolveMediaPath(*action.StickerID)
				if presolveErr != nil {
					a.logger.Warn("Rejected sticker path outside managed temp dir", zap.String("reported_sticker_id", *action.StickerID), zap.Error(presolveErr))
				} else if f, ferr := os.Open(resolvedPath); ferr != nil {
					a.logger.Warn("Sticker file not found", zap.String("path", resolvedPath), zap.Error(ferr))
				} else {
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
					a.logger.Info("Sticker action processed for Telegram", zap.Int64("chat_id", action.ChatID), zap.String("sticker_id", *action.StickerID))
				}
			} else {
				status = "failed"
				a.logger.Error("Failed to resolve peer for sticker action", zap.Int64("chat_id", action.ChatID), zap.Error(perr))
			}
		}

	default:
		// Stream Reasoning Chunk Buffering:
		// Accumulate text_content into textBuffer[action.ChatID] while IsFinal is false.
		// Only send ONE single consolidated Telegram message when IsFinal becomes true!
		a.peerMu.Lock()
		if action.TextContent != nil && *action.TextContent != "" {
			a.textBuffer[action.ChatID] = append(a.textBuffer[action.ChatID], *action.TextContent)
		}

		if !action.IsFinal {
			a.peerMu.Unlock()
			// Keep typing heartbeat active and maintain streaming state for non-final chunks
			a.stateMachine.TransitionToChat(action.ChatID, engine.StateStreamingTTS, "stream_reasoning_chunk")
			a.stateMachine.TouchWatchdogChat(action.ChatID, 30*time.Second)
			return
		}

		// IsFinal == true: Flush and send consolidated Telegram message
		fullText := strings.Join(a.textBuffer[action.ChatID], "")
		delete(a.textBuffer, action.ChatID) // Actually drop the key, not just nil the slice
		a.peerMu.Unlock()

		if fullText != "" {
			inputPeer, perr := a.resolveInputPeer(ctx, action.ChatID)
			if perr == nil {
				target := a.sender.To(inputPeer)
				_, err := target.Text(ctx, fullText)
				if err != nil {
					status = "failed"
					errStr := err.Error()
					errDetail = &errStr
					a.logger.Error("Failed to send consolidated message to Telegram", zap.Int64("chat_id", action.ChatID), zap.Error(err))
				} else {
					a.logger.Info("Sent consolidated message to Telegram", zap.Int64("chat_id", action.ChatID), zap.Int("total_len", len(fullText)))
				}
			} else {
				status = "failed"
				a.logger.Error("Failed to resolve input peer for send_message", zap.Int64("chat_id", action.ChatID), zap.Error(perr))
			}
		}
	}

	// State Machine Management for Telegram Streaming:
	if !action.IsFinal {
		a.stateMachine.TransitionToChat(action.ChatID, engine.StateStreamingTTS, "stream_reasoning_chunk")
		a.stateMachine.TouchWatchdogChat(action.ChatID, 30*time.Second)
	} else {
		// IsFinal == true: Set 5-second smooth audio/send buffer window instead of jumping immediately to IDLE
		a.stateMachine.TouchWatchdogChat(action.ChatID, 5*time.Second)
		if a.urgeEngine != nil {
			a.urgeEngine.OnTurnCompleted()
		}
	}

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

// pruneStalePeersLocked sweeps peerAccessHashes/peerUsernames/peerLastSeen of
// entries not seen for peerCacheIdleTTL, gated by peerCachePruneEvery so it
// doesn't do a full scan on every call. Caller must hold a.peerMu (write lock).
func (a *GotdAdapter) pruneStalePeersLocked() {
	now := time.Now()
	if now.Sub(a.peerCachePruned) < peerCachePruneEvery {
		return
	}
	a.peerCachePruned = now

	for peerID, seen := range a.peerLastSeen {
		if now.Sub(seen) > peerCacheIdleTTL {
			delete(a.peerAccessHashes, peerID)
			delete(a.peerUsernames, peerID)
			delete(a.peerLastSeen, peerID)
		}
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
				a.peerLastSeen[chatID] = time.Now()
				if u.Username != "" {
					a.peerUsernames[chatID] = u.Username
				}
				a.pruneStalePeersLocked()
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

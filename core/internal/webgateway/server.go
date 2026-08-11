package webgateway

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"math/big"
	"net/http"
	"strconv"

	"go.uber.org/zap"
	"nhooyr.io/websocket"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/engine"
)

// WebNamespaceOffset partitions all WebGateway-originated chat IDs into a
// range that Telegram's numeric user/channel IDs can never reach, so a web
// client can never address (and thus never read the memory of) a real
// Telegram chat, even with a valid WEBGATEWAY_TOKEN.
const WebNamespaceOffset int64 = 9_000_000_000_000_000

type Server struct {
	addr           string
	token          string
	allowedOrigins []string
	sessions       *SessionManager
	bridge         *NatsBridge
	logger         *zap.Logger
	httpServer     *http.Server
}

func NewServer(
	addr string,
	token string,
	allowedOrigins []string,
	bus *bus.NatsBus,
	csm *engine.CentralStateMachine,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	circadian *emotion.CircadianRhythmEvaluator,
	logger *zap.Logger,
) *Server {
	sessions := newSessionManager(logger)
	bridge := newNatsBridge(bus, sessions, csm, emoState, personality, circadian, logger)

	return &Server{
		addr:           addr,
		token:          token,
		allowedOrigins: allowedOrigins,
		sessions:       sessions,
		bridge:         bridge,
		logger:         logger,
	}
}

func (s *Server) Start() error {
	if err := s.bridge.StartSubscriptions(); err != nil {
		s.logger.Warn("WebGateway failed to start NATS subscriptions (bus might be offline)", zap.Error(err))
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/ws", s.handleWebSocket)

	s.httpServer = &http.Server{
		Addr:    s.addr,
		Handler: mux,
	}

	s.logger.Info("🚀 WebGateway WebSocket Server starting", zap.String("addr", s.addr), zap.String("ws_path", "/ws"))

	go func() {
		if err := s.httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			s.logger.Error("WebGateway HTTP Server error", zap.Error(err))
		}
	}()

	return nil
}

func (s *Server) Stop(ctx context.Context) error {
	if s.httpServer != nil {
		return s.httpServer.Shutdown(ctx)
	}
	return nil
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok","service":"webgateway"}`))
}

func (s *Server) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	// Reject before upgrading: an anonymous WS client could otherwise pick
	// any chat_id and read/inject into another user's conversation.
	suppliedToken := r.URL.Query().Get("token")
	if subtle.ConstantTimeCompare([]byte(suppliedToken), []byte(s.token)) != 1 {
		s.logger.Warn("Rejected WebSocket handshake with invalid/missing token", zap.String("remote_addr", r.RemoteAddr))
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	// The token check above is the real access control. Origin is a second,
	// browser-only layer: if WEBGATEWAY_ALLOWED_ORIGINS is configured, only
	// those origins may open cross-origin WebSockets (same-host and
	// non-browser clients without an Origin header are always allowed
	// regardless). Left unset, Origin isn't checked at all -- e.g. for a
	// frontend served from a different domain than the Go core.
	acceptOpts := &websocket.AcceptOptions{}
	if len(s.allowedOrigins) > 0 {
		acceptOpts.OriginPatterns = s.allowedOrigins
	} else {
		acceptOpts.InsecureSkipVerify = true
	}

	conn, err := websocket.Accept(w, r, acceptOpts)
	if err != nil {
		s.logger.Warn("Failed to accept WebSocket upgrade", zap.Error(err))
		return
	}

	// Set WebSocket read limit to 16MB (16 * 1024 * 1024 bytes) to allow high-res Base64 vision frames
	conn.SetReadLimit(16 * 1024 * 1024)

	// 1. Dynamic ChatID Resolution: Parse chat_id from URL query params (e.g. /ws?chat_id=1002)
	chatID := parseOrGenerateChatID(r)

	session := newClientSession(chatID, conn, s.logger)
	s.sessions.Register(session)
	defer s.sessions.Unregister(session)

	// Start write loop in a separate goroutine
	go session.writeLoop()

	// Read loop in the current handler goroutine
	for {
		typ, data, err := conn.Read(r.Context())
		if err != nil {
			if websocket.CloseStatus(err) != websocket.StatusNormalClosure && websocket.CloseStatus(err) != websocket.StatusGoingAway {
				s.logger.Debug("WebSocket read error", zap.String("session_id", session.ID), zap.Error(err))
			}
			break
		}

		if typ == websocket.MessageText || typ == websocket.MessageBinary {
			s.bridge.HandleUserWSMessage(session, typ, data)
		}
	}
}

// parseOrGenerateChatID resolves the chat_id for a new WebSocket session.
// A client-supplied sub-id (for reconnecting to the same session across tabs)
// is honored, but always folded into WebNamespaceOffset so it can never
// collide with a real Telegram chat/user ID.
func parseOrGenerateChatID(r *http.Request) int64 {
	rawChatID := r.URL.Query().Get("chat_id")
	if rawChatID != "" {
		if parsed, err := strconv.ParseInt(rawChatID, 10, 64); err == nil && parsed > 0 {
			return WebNamespaceOffset + parsed
		}
	}

	// Fallback: Generate random 64-bit sub-id in range [1000000, 999999999] for isolated web sessions
	nBig, err := rand.Int(rand.Reader, big.NewInt(998999999))
	if err == nil {
		return WebNamespaceOffset + nBig.Int64() + 1000000
	}

	return WebNamespaceOffset + 900000001
}

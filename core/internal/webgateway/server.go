package webgateway

import (
	"context"
	"crypto/rand"
	"math/big"
	"net/http"
	"strconv"

	"go.uber.org/zap"
	"nhooyr.io/websocket"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/engine"
)

type Server struct {
	addr       string
	sessions   *SessionManager
	bridge     *NatsBridge
	logger     *zap.Logger
	httpServer *http.Server
}

func NewServer(
	addr string,
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
		addr:     addr,
		sessions: sessions,
		bridge:   bridge,
		logger:   logger,
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
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		InsecureSkipVerify: true, // Allow cross-origin WebSocket connections from local stage-web dev server
	})
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

func parseOrGenerateChatID(r *http.Request) int64 {
	rawChatID := r.URL.Query().Get("chat_id")
	if rawChatID != "" {
		if parsed, err := strconv.ParseInt(rawChatID, 10, 64); err == nil && parsed > 0 {
			return parsed
		}
	}

	// Fallback: Generate random 64-bit ID in range [1000000, 999999999] for isolated web sessions
	nBig, err := rand.Int(rand.Reader, big.NewInt(998999999))
	if err == nil {
		return nBig.Int64() + 1000000
	}

	return 900000001
}

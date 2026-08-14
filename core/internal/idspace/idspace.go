// Package idspace defines the chat_id numbering scheme shared across
// packages that otherwise cannot import each other without a cycle (e.g.
// webgateway imports engine, so engine cannot import webgateway back to
// find out whether a chat_id belongs to a web session).
package idspace

// WebNamespaceOffset partitions all WebGateway-originated chat IDs into a
// range that Telegram's numeric user/channel IDs can never reach, so a web
// client can never address (and thus never read the memory of) a real
// Telegram chat, even with a valid WEBGATEWAY_TOKEN. See docs/SECURITY.md §2.2.
const WebNamespaceOffset int64 = 9_000_000_000_000_000

// IsWebChat reports whether chatID falls in the WebGateway namespace.
func IsWebChat(chatID int64) bool {
	return chatID >= WebNamespaceOffset
}

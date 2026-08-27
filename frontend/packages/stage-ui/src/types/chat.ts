import type { ChatHistoryItem as CoreChatHistoryItem } from '@proj-airi/core-agent'

export type ChatHistoryItem = CoreChatHistoryItem & { voice?: { transcript: string } }

export type {
  ChatAssistantMessage,
  ChatMessage,
  ChatSlices,
  ChatSlicesText,
  ChatSlicesToolCall,
  ChatSlicesToolCallResult,
  ChatStreamEvent,
  ChatStreamEventContext,
  ContextMessage,
  ErrorMessage,
  StreamingAssistantMessage,
} from '@proj-airi/core-agent'

import * as path from 'node:path'
import * as vscode from 'vscode'

// IPC contract with services/mcp_vscode/server.py's vscode_highlight_range /
// vscode_clear_highlight tools: that process writes this file (see
// --signal-path / betteragent-highlighter.signalPath, which must match), we
// only ever read it. No network port, no auth -- same-machine file handoff.
interface HighlightSignal {
  action: 'highlight'
  path: string
  start_line: number
  end_line: number
  label?: string
}
interface ClearSignal {
  action: 'clear'
}
type Signal = HighlightSignal | ClearSignal

const decorationStyle = vscode.window.createTextEditorDecorationType({
  backgroundColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
  isWholeLine: true,
  overviewRulerColor: new vscode.ThemeColor('editorOverviewRuler.findMatchForeground'),
  overviewRulerLane: vscode.OverviewRulerLane.Full,
})

let activeEditor: vscode.TextEditor | undefined

function resolveSignalPath(): vscode.Uri {
  const configured = vscode.workspace.getConfiguration('betteragent-highlighter').get<string>(
    'signalPath',
    'temp/vscode_highlight_signal.json',
  )
  if (path.isAbsolute(configured)) {
    return vscode.Uri.file(configured)
  }
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? process.cwd()
  return vscode.Uri.file(path.join(root, configured))
}

async function clearHighlight(): Promise<void> {
  activeEditor?.setDecorations(decorationStyle, [])
  activeEditor = undefined
}

async function applyHighlight(signal: HighlightSignal): Promise<void> {
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(signal.path))
  const editor = await vscode.window.showTextDocument(document, { preserveFocus: false })

  const lineCount = document.lineCount
  const startLine = Math.max(0, Math.min(signal.start_line - 1, lineCount - 1))
  const endLine = Math.max(startLine, Math.min(signal.end_line - 1, lineCount - 1))
  const range = new vscode.Range(startLine, 0, endLine, document.lineAt(endLine).text.length)

  editor.setDecorations(decorationStyle, [{
    range,
    hoverMessage: signal.label,
  }])
  editor.revealRange(range, vscode.TextEditorRevealType.InCenter)
  activeEditor = editor
}

async function handleSignalChange(uri: vscode.Uri): Promise<void> {
  let raw: Uint8Array
  try {
    raw = await vscode.workspace.fs.readFile(uri)
  }
  catch {
    return
  }

  let signal: Signal
  try {
    signal = JSON.parse(Buffer.from(raw).toString('utf-8'))
  }
  catch {
    // Signal file mid-write (non-atomic write on the Python side); the next
    // change event will retry.
    return
  }

  if (signal.action === 'clear') {
    await clearHighlight()
  }
  else if (signal.action === 'highlight') {
    await applyHighlight(signal)
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const signalUri = resolveSignalPath()
  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(vscode.Uri.file(path.dirname(signalUri.fsPath)), path.basename(signalUri.fsPath)),
  )

  watcher.onDidCreate(handleSignalChange)
  watcher.onDidChange(handleSignalChange)

  context.subscriptions.push(
    watcher,
    decorationStyle,
    vscode.commands.registerCommand('betteragent-highlighter.clear', clearHighlight),
  )
}

export function deactivate(): void {
  activeEditor?.setDecorations(decorationStyle, [])
}

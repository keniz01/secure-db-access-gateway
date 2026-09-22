import { useRef, useCallback, useEffect } from 'react';
import Editor, { type OnMount } from '@monaco-editor/react';
import type { editor } from 'monaco-editor';
import type { IDisposable } from 'monaco-editor';
import type { SchemaTable } from '../dashboard/SchemaBrowser';
import { createSqlCompletionProvider } from './SqlCompletionProvider';

interface SqlMonacoEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  height?: string;
  onRunQuery?: () => void;
  readOnly?: boolean;
  schemaTables?: SchemaTable[];
}

export const SqlMonacoEditor = ({
  value,
  onChange,
  placeholder,
  height = '160px',
  onRunQuery,
  readOnly = false,
  schemaTables,
}: SqlMonacoEditorProps) => {
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const disposableRef = useRef<IDisposable | null>(null);

  const handleMount: OnMount = useCallback(
    (editorInstance, monaco) => {
      editorRef.current = editorInstance;

      // Register Cmd+Enter / Ctrl+Enter to run query
      if (onRunQuery) {
        editorInstance.addAction({
          id: 'run-query',
          label: 'Run Query',
          keybindings: [
            monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter,
          ],
          run: () => onRunQuery(),
        });
      }

      // Register SQL autocomplete provider
      if (schemaTables && schemaTables.length > 0) {
        const provider = createSqlCompletionProvider(schemaTables);
        disposableRef.current = monaco.languages.registerCompletionItemProvider('sql', provider);
      }

      // Placeholder text via content widget
      if (placeholder) {
        const node = document.createElement('div');
        node.textContent = placeholder;
        node.style.color = '#9ca3af';
        node.style.fontFamily = 'monospace';
        node.style.fontSize = '13px';
        node.style.lineHeight = '1.5';
        node.style.padding = '4px 8px';
        node.style.pointerEvents = 'none';
        node.style.position = 'absolute';

        const placeholderWidget: editor.IContentWidget = {
          getId: () => 'placeholder.widget',
          getDomNode: () => node,
          getPosition: () => ({
            position: { lineNumber: 1, column: 1 },
            range: new monaco.Range(1, 1, 1, 1),
            preference: [monaco.editor.ContentWidgetPositionPreference.EXACT],
          }),
        };

        const updatePlaceholder = () => {
          if (editorInstance.getValue() === '' && placeholder) {
            editorInstance.addContentWidget(placeholderWidget);
          } else {
            editorInstance.removeContentWidget(placeholderWidget);
          }
        };

        updatePlaceholder();
        editorInstance.onDidChangeModelContent(updatePlaceholder);
      }

      // Focus editor on mount
      editorInstance.focus();
    },
    [onRunQuery, placeholder, schemaTables]
  );

  // Dispose completion provider on unmount or schemaTables change
  useEffect(() => {
    return () => {
      if (disposableRef.current) {
        disposableRef.current.dispose();
        disposableRef.current = null;
      }
    };
  }, [schemaTables]);

  const handleChange = useCallback(
    (val: string | undefined) => {
      onChange(val ?? '');
    },
    [onChange]
  );

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden focus-within:ring-2 focus-within:ring-indigo-500 focus-within:border-indigo-500">
      <Editor
        height={height}
        language="sql"
        theme="vs-dark"
        value={value}
        onChange={handleChange}
        onMount={handleMount}
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace",
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          automaticLayout: true,
          tabSize: 2,
          padding: { top: 8, bottom: 8 },
          renderLineHighlight: 'line',
          cursorBlinking: 'smooth',
          smoothScrolling: true,
          readOnly,
        }}
      />
    </div>
  );
};

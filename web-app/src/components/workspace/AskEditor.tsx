import { useState } from 'react';
import { Play, Sparkles, Pencil, Trash2 } from 'lucide-react';
import { SqlMonacoEditor } from './SqlMonacoEditor';

interface AskEditorProps {
  question: string;
  generatedSql: string | null;
  onQuestionChange: (question: string) => void;
  onGeneratedSqlChange: (sql: string) => void;
  onGenerateSql: () => void;
  onExecute: () => void;
  onClear: () => void;
  isGenerating: boolean;
  isExecuting: boolean;
}

export const AskEditor = ({
  question,
  generatedSql,
  onQuestionChange,
  onGeneratedSqlChange,
  onGenerateSql,
  onExecute,
  onClear,
  isGenerating,
  isExecuting,
}: AskEditorProps) => {
  const [editingGenerated, setEditingGenerated] = useState(false);

  return (
    <div className="space-y-4">
      {/* Question input — plain textarea, not SQL */}
      <div className="relative">
        <textarea
          value={question}
          onChange={(e) => onQuestionChange(e.target.value)}
          placeholder="Ask a question about your data..."
          className="w-full h-24 p-4 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm leading-relaxed resize-y bg-white"
        />
      </div>

      {/* Generate button */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onGenerateSql}
          disabled={isGenerating || !question.trim()}
          className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md transition-colors"
        >
          {isGenerating ? (
            <>
              <div className="animate-spin rounded-full h-3.5 w-3.5 border-2 border-white border-t-transparent" />
              Generating...
            </>
          ) : (
            <>
              <Sparkles size={14} />
              Generate SQL
            </>
          )}
        </button>
      </div>

      {/* Generated SQL */}
      {generatedSql && (
        <div className="border border-indigo-200 rounded-lg bg-indigo-50/50 overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-indigo-200 bg-indigo-100/50">
            <span className="text-xs font-medium text-indigo-700">Generated SQL</span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setEditingGenerated(!editingGenerated)}
                className="inline-flex items-center gap-1 px-2 py-1 text-xs text-indigo-600 hover:bg-indigo-100 rounded transition-colors"
              >
                <Pencil size={11} />
                {editingGenerated ? 'Done' : 'Edit'}
              </button>
            </div>
          </div>
          {editingGenerated ? (
            <SqlMonacoEditor
              value={generatedSql}
              onChange={onGeneratedSqlChange}
              onRunQuery={onExecute}
              height="120px"
            />
          ) : (
            <pre className="p-3 font-mono text-sm leading-relaxed text-gray-700 overflow-x-auto">
              {generatedSql}
            </pre>
          )}
        </div>
      )}

      {/* Execute / Clear */}
      {generatedSql && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onExecute}
            disabled={isExecuting || !generatedSql.trim()}
            className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md transition-colors"
          >
            {isExecuting ? (
              <>
                <div className="animate-spin rounded-full h-3.5 w-3.5 border-2 border-white border-t-transparent" />
                Running...
              </>
            ) : (
              <>
                <Play size={14} />
                Run query
              </>
            )}
          </button>
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-md transition-colors"
          >
            <Trash2 size={14} />
            Clear
          </button>
          <span className="ml-auto text-xs text-gray-400">
            ⌘+Enter to run
          </span>
        </div>
      )}
    </div>
  );
};

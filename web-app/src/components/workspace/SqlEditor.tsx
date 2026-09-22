import { Play, Trash2 } from 'lucide-react';
import { SqlMonacoEditor } from './SqlMonacoEditor';
import type { SchemaTable } from '../dashboard/SchemaBrowser';

interface SqlEditorProps {
  sql: string;
  onSqlChange: (sql: string) => void;
  onExecute: () => void;
  onClear: () => void;
  isExecuting: boolean;
  schemaTables?: SchemaTable[];
}

export const SqlEditor = ({
  sql,
  onSqlChange,
  onExecute,
  onClear,
  isExecuting,
  schemaTables,
}: SqlEditorProps) => {
  return (
    <div className="space-y-3">
      <SqlMonacoEditor
        value={sql}
        onChange={onSqlChange}
        onRunQuery={onExecute}
        placeholder="SELECT * FROM ..."
        height="160px"
        schemaTables={schemaTables}
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onExecute}
          disabled={isExecuting || !sql.trim()}
          className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md transition-colors"
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
    </div>
  );
};

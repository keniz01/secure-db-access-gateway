import { MessageSquare, Code } from 'lucide-react';

export type QueryMode = 'ask' | 'sql';

interface QueryModeSwitcherProps {
  mode: QueryMode;
  onModeChange: (mode: QueryMode) => void;
}

export const QueryModeSwitcher = ({ mode, onModeChange }: QueryModeSwitcherProps) => {
  return (
    <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
      <button
        type="button"
        onClick={() => onModeChange('ask')}
        className={`flex items-center gap-1.5 px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
          mode === 'ask'
            ? 'bg-white text-indigo-700 shadow-sm border border-indigo-200'
            : 'text-gray-500 hover:text-gray-700'
        }`}
      >
        <MessageSquare size={14} />
        Ask
      </button>
      <button
        type="button"
        onClick={() => onModeChange('sql')}
        className={`flex items-center gap-1.5 px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
          mode === 'sql'
            ? 'bg-white text-indigo-700 shadow-sm border border-indigo-200'
            : 'text-gray-500 hover:text-gray-700'
        }`}
      >
        <Code size={14} />
        SQL
      </button>
    </div>
  );
};

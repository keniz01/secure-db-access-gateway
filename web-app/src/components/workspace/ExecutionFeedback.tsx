import { CheckCircle, AlertTriangle, Loader2, XCircle } from 'lucide-react';

interface PolicyCheck {
  label: string;
  passed: boolean;
}

interface ExecutionFeedbackProps {
  status: 'idle' | 'validating' | 'executing' | 'success' | 'error';
  checks?: PolicyCheck[];
  databaseId?: string;
  appliedControls?: string[];
  errorMessage?: string;
  onCancel?: () => void;
}

export const ExecutionFeedback = ({
  status,
  checks = [],
  databaseId,
  appliedControls = [],
  errorMessage,
  onCancel,
}: ExecutionFeedbackProps) => {
  if (status === 'idle') return null;

  return (
    <div className="border border-gray-200 rounded-lg bg-white overflow-hidden text-sm">
      {/* Validating */}
      {status === 'validating' && (
        <div className="flex items-center gap-2 px-4 py-3 text-gray-600">
          <Loader2 size={16} className="animate-spin text-indigo-600" />
          Validating query...
        </div>
      )}

      {/* Policy checks */}
      {status === 'executing' && checks.length > 0 && (
        <div className="px-4 py-3 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
              Policy checks
            </span>
            {onCancel && (
              <button
                type="button"
                onClick={onCancel}
                className="text-xs text-red-500 hover:text-red-700"
              >
                Cancel
              </button>
            )}
          </div>
          {checks.map((check) => (
            <div key={check.label} className="flex items-center gap-2">
              {check.passed ? (
                <CheckCircle size={14} className="text-green-500 shrink-0" />
              ) : (
                <XCircle size={14} className="text-red-500 shrink-0" />
              )}
              <span className={check.passed ? 'text-gray-700' : 'text-red-600'}>
                {check.label}
              </span>
            </div>
          ))}
          {databaseId && (
            <div className="pt-1.5 mt-1.5 border-t border-gray-100 text-xs text-gray-500">
              Executing against <span className="font-medium">{databaseId}</span>
            </div>
          )}
        </div>
      )}

      {/* Applied controls */}
      {status === 'success' && appliedControls.length > 0 && (
        <div className="px-4 py-3 bg-amber-50 border-b border-amber-100">
          <div className="flex items-center gap-2 mb-1.5">
            <AlertTriangle size={14} className="text-amber-600" />
            <span className="text-xs font-medium text-amber-800">Query executed with policy controls</span>
          </div>
          <ul className="space-y-0.5 ml-5">
            {appliedControls.map((control) => (
              <li key={control} className="text-xs text-amber-700 list-disc">
                {control}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Error */}
      {status === 'error' && errorMessage && (
        <div className="flex items-start gap-2 px-4 py-3 bg-red-50 border-b border-red-100">
          <XCircle size={16} className="text-red-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-red-800">Query failed</p>
            <p className="text-xs text-red-600 mt-1">{errorMessage}</p>
          </div>
        </div>
      )}
    </div>
  );
};

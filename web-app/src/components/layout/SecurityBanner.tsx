import { Database, Shield } from 'lucide-react';

interface SecurityBannerProps {
  databaseId: string;
  isReadOnly: boolean;
  role?: string;
  rowLimit?: number;
}

export const SecurityBanner = ({
  databaseId,
  isReadOnly,
  role,
  rowLimit,
}: SecurityBannerProps) => {
  return (
    <div className="bg-indigo-50 border-b border-indigo-100 px-4 py-2 shrink-0">
      <div className="flex items-center gap-4 text-xs text-indigo-700">
        <div className="flex items-center gap-1.5">
          <Database size={13} />
          <span className="font-medium">{databaseId}</span>
        </div>
        <div className="w-px h-3 bg-indigo-200" />
        <div className="flex items-center gap-1.5">
          <span>PostgreSQL</span>
        </div>
        <div className="w-px h-3 bg-indigo-200" />
        <div className="flex items-center gap-1.5">
          <Shield size={13} />
          <span className="font-medium">
            {isReadOnly ? 'Read-only' : 'Read/Write'}
          </span>
        </div>
        {role && (
          <>
            <div className="w-px h-3 bg-indigo-200" />
            <span className="text-indigo-500">Role: {role}</span>
          </>
        )}
        {rowLimit && (
          <>
            <div className="w-px h-3 bg-indigo-200" />
            <span className="text-indigo-500">{rowLimit.toLocaleString()} row limit</span>
          </>
        )}
      </div>
    </div>
  );
};

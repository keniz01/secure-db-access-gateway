import { useState, useCallback, useRef, useEffect } from 'react';
import { PanelLeftClose, PanelLeft } from 'lucide-react';

interface AppLayoutProps {
  securityBanner: React.ReactNode;
  schemaSidebar: React.ReactNode;
  queryArea: React.ReactNode;
  resultsArea: React.ReactNode;
  onLogout: () => void;
}

const MIN_SIDEBAR_WIDTH = 240;
const MAX_SIDEBAR_WIDTH = 480;
const DEFAULT_SIDEBAR_WIDTH = 300;
const SIDEBAR_WIDTH_KEY = 'dg:sidebar-width';
const SIDEBAR_COLLAPSED_KEY = 'dg:sidebar-collapsed';

const readStoredWidth = (): number => {
  try {
    const stored = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    if (stored !== null) {
      const n = Number(stored);
      if (n >= MIN_SIDEBAR_WIDTH && n <= MAX_SIDEBAR_WIDTH) return n;
    }
  } catch { /* localStorage unavailable */ }
  return DEFAULT_SIDEBAR_WIDTH;
};

const readStoredCollapsed = (): boolean => {
  try {
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
  } catch { /* localStorage unavailable */ }
  return false;
};

export const AppLayout = ({
  securityBanner,
  schemaSidebar,
  queryArea,
  resultsArea,
  onLogout,
}: AppLayoutProps) => {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readStoredCollapsed);
  const [sidebarWidth, setSidebarWidth] = useState(readStoredWidth);
  const isResizing = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Persist sidebar state
  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth));
    } catch { /* localStorage unavailable */ }
  }, [sidebarWidth]);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(sidebarCollapsed));
    } catch { /* localStorage unavailable */ }
  }, [sidebarCollapsed]);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const newWidth = Math.max(
        MIN_SIDEBAR_WIDTH,
        Math.min(MAX_SIDEBAR_WIDTH, e.clientX - rect.left)
      );
      setSidebarWidth(newWidth);
    };

    const handleMouseUp = () => {
      if (isResizing.current) {
        isResizing.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  return (
    <div className="h-screen flex flex-col bg-gray-50 overflow-hidden">
      {/* Top bar */}
      <header className="h-12 bg-white border-b border-gray-200 flex items-center justify-between px-4 shrink-0">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setSidebarCollapsed((c) => !c)}
            className="p-1.5 rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
            title={sidebarCollapsed ? 'Show schema' : 'Hide schema'}
          >
            {sidebarCollapsed ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
          </button>
          <span className="text-sm font-semibold text-gray-800">Data Gateway</span>
        </div>
        <button
          onClick={onLogout}
          className="px-3 py-1.5 text-sm font-medium text-gray-600 hover:text-red-600 hover:bg-red-50 rounded-md transition-colors"
        >
          Logout
        </button>
      </header>

      {/* Security banner */}
      {securityBanner}

      {/* Main content */}
      <div ref={containerRef} className="flex-1 flex overflow-hidden">
        {/* Schema sidebar */}
        <aside
          style={{ width: sidebarCollapsed ? 0 : sidebarWidth }}
          className="shrink-0 border-r border-gray-200 bg-white overflow-hidden transition-[width] duration-200 relative"
        >
          <div className="absolute inset-0 overflow-y-auto">
            {schemaSidebar}
          </div>
          {/* Resize handle */}
          {!sidebarCollapsed && (
            <div
              onMouseDown={handleResizeStart}
              className="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-indigo-300 bg-transparent transition-colors z-10"
            />
          )}
        </aside>

        {/* Main workspace */}
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {/* Query area */}
          <div className="flex-1 overflow-y-auto p-4">
            {queryArea}
          </div>

          {/* Results area */}
          <div className="flex-1 overflow-y-auto border-t border-gray-200 bg-white p-4">
            {resultsArea}
          </div>
        </main>
      </div>
    </div>
  );
};

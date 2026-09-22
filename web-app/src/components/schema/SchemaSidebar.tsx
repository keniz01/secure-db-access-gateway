import { useState, useMemo, useEffect } from 'react';
import { Search, ChevronRight, ChevronDown, Key, Link } from 'lucide-react';
import type { SchemaTable } from '../dashboard/SchemaBrowser';

const EXPANDED_TABLES_KEY = 'dg:expanded-tables';

const readStoredExpanded = (): Set<string> => {
  try {
    const stored = localStorage.getItem(EXPANDED_TABLES_KEY);
    if (stored) {
      const arr = JSON.parse(stored);
      if (Array.isArray(arr)) return new Set(arr);
    }
  } catch { /* localStorage unavailable */ }
  return new Set();
};

interface SchemaSidebarProps {
  tables: SchemaTable[];
  isLoading: boolean;
  error: string | null;
  onInsertIdentifier?: (identifier: string) => void;
}

export const SchemaSidebar = ({
  tables,
  isLoading,
  error,
  onInsertIdentifier,
}: SchemaSidebarProps) => {
  const [search, setSearch] = useState('');
  const [expandedTables, setExpandedTables] = useState<Set<string>>(readStoredExpanded);
  const [selectedColumn, setSelectedColumn] = useState<{ table: string; column: string } | null>(null);

  // Persist expanded tables
  useEffect(() => {
    try {
      localStorage.setItem(EXPANDED_TABLES_KEY, JSON.stringify([...expandedTables]));
    } catch { /* localStorage unavailable */ }
  }, [expandedTables]);

  const filteredTables = useMemo(() => {
    if (!search.trim()) return tables;
    const q = search.toLowerCase();
    return tables.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.columns.some((c) => c.name.toLowerCase().includes(q))
    );
  }, [tables, search]);

  const toggleTable = (name: string) => {
    setExpandedTables((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const handleColumnClick = (tableName: string, columnName: string) => {
    setSelectedColumn({ table: tableName, column: columnName });
    onInsertIdentifier?.(`${tableName}.${columnName}`);
  };

  const handleTableClick = (tableName: string) => {
    onInsertIdentifier?.(tableName);
  };

  if (isLoading) {
    return (
      <div className="p-4">
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-indigo-600" />
          Loading schema...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4">
        <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-md p-3">
          Schema unavailable
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Search */}
      <div className="p-3 border-b border-gray-100">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search tables & columns..."
            className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-md focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 bg-gray-50"
          />
        </div>
      </div>

      {/* Table list */}
      <div className="flex-1 overflow-y-auto">
        {filteredTables.length === 0 ? (
          <div className="p-4 text-sm text-gray-500 text-center">
            {search ? 'No matching tables' : 'No tables found'}
          </div>
        ) : (
          <div className="py-1">
            {filteredTables.map((table) => {
              const isExpanded = expandedTables.has(table.name);
              return (
                <div key={table.name}>
                  {/* Table row */}
                  <button
                    type="button"
                    onClick={() => toggleTable(table.name)}
                    className="w-full flex items-center gap-1.5 px-3 py-1.5 text-sm text-left hover:bg-gray-50 group"
                  >
                    <span className="text-gray-400 shrink-0">
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </span>
                    <span
                      className="font-medium text-gray-700 group-hover:text-indigo-600 truncate cursor-pointer"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleTableClick(table.name);
                      }}
                      title={`Insert ${table.name}`}
                    >
                      {table.name}
                    </span>
                    <span className="ml-auto text-xs text-gray-400 shrink-0">
                      {table.columns.length}
                    </span>
                  </button>

                  {/* Expanded columns */}
                  {isExpanded && (
                    <div className="ml-5 border-l border-gray-100">
                      {table.columns.map((col) => (
                        <button
                          key={col.name}
                          type="button"
                          onClick={() => handleColumnClick(table.name, col.name)}
                          className={`w-full flex items-center gap-2 px-3 py-1 text-xs text-left hover:bg-indigo-50 group ${
                            selectedColumn?.table === table.name && selectedColumn?.column === col.name
                              ? 'bg-indigo-50 text-indigo-700'
                              : 'text-gray-600'
                          }`}
                          title={`${col.type}${col.isPrimary ? ' (PK)' : ''}`}
                        >
                          {col.isPrimary && <Key size={10} className="text-amber-500 shrink-0" />}
                          <span className="truncate font-mono">{col.name}</span>
                          <span className="ml-auto text-gray-400 shrink-0 font-mono text-[10px]">
                            {col.type}
                          </span>
                        </button>
                      ))}

                      {/* Foreign keys */}
                      {table.foreignKeys.length > 0 && (
                        <div className="px-3 py-1.5 mt-1 border-t border-gray-50">
                          <div className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">
                            Foreign keys
                          </div>
                          {table.foreignKeys.map((fk) => (
                            <div
                              key={`${fk.column}-${fk.foreignTable}`}
                              className="flex items-center gap-1.5 text-[11px] text-gray-500 py-0.5"
                            >
                              <Link size={9} className="shrink-0" />
                              <span className="font-mono">{fk.column}</span>
                              <span className="text-gray-400">→</span>
                              <span className="font-mono">{fk.foreignTable}.{fk.foreignColumn}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer count */}
      <div className="px-3 py-2 border-t border-gray-100 text-xs text-gray-400">
        {tables.length} tables
      </div>
    </div>
  );
};

import { useState } from 'react';

export interface SchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
  isPrimary: boolean;
}

export interface SchemaForeignKey {
  column: string;
  foreignTable: string;
  foreignColumn: string;
}

export interface SchemaTable {
  name: string;
  schemaName: string;
  columns: SchemaColumn[];
  foreignKeys: SchemaForeignKey[];
}

interface SchemaBrowserProps {
  tables: SchemaTable[];
  isLoading: boolean;
  error: string | null;
}

export const SchemaBrowser = ({ tables, isLoading, error }: SchemaBrowserProps) => {
  const [selectedTableName, setSelectedTableName] = useState<string | null>(tables[0]?.name ?? null);

  if (isLoading) {
    return (
      <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
        <h3 className="text-xl font-bold text-gray-800 mb-6">Schema Browser</h3>
        <div className="flex items-center justify-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 mb-4"></div>
          <p className="ml-4 text-gray-600">Loading database schema...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
        <h3 className="text-xl font-bold text-gray-800 mb-6">Schema Browser</h3>
        <div className="bg-amber-50 border-l-4 border-amber-500 p-4 rounded-lg">
          <p className="text-amber-800 font-semibold">Schema unavailable</p>
          <p className="text-amber-700 mt-2">{error}</p>
        </div>
      </div>
    );
  }

  if (!tables.length) {
    return (
      <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
        <h3 className="text-xl font-bold text-gray-800 mb-6">Schema Browser</h3>
        <div className="text-center py-12 text-gray-500">
          No tables were returned by the connected database.
        </div>
      </div>
    );
  }

  const selectedTable = tables.find((table) => table.name === selectedTableName) ?? tables[0];

  return (
    <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
      <div className="flex items-center justify-between mb-6">
        <h3 className="text-xl font-bold text-gray-800">Schema Browser</h3>
        <span className="text-sm text-gray-500">{tables.length} tables</span>
      </div>

      <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="border-r border-gray-200 pr-4">
          <div className="space-y-2">
            {tables.map((table) => (
              <button
                key={table.name}
                type="button"
                onClick={() => setSelectedTableName(table.name)}
                className={`w-full text-left px-4 py-3 rounded-lg border transition-colors duration-150 ${
                  selectedTable.name === table.name
                    ? 'bg-indigo-50 border-indigo-200 text-indigo-700'
                    : 'bg-gray-50 border-gray-200 text-gray-700 hover:bg-gray-100'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{table.name}</span>
                  <span className="text-xs bg-white px-2 py-1 rounded-full text-gray-500">
                    {table.columns.length}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="min-w-0">
          <div className="mb-6">
            <h4 className="text-2xl font-bold text-gray-800">{selectedTable.name}</h4>
            <p className="text-sm text-gray-500">Schema: {selectedTable.schemaName}</p>
          </div>

          <div className="mb-8">
            <h5 className="text-lg font-semibold text-gray-800 mb-3">Columns</h5>
            <div className="overflow-x-auto rounded-lg border border-gray-200">
              <table className="min-w-full text-left text-sm text-gray-700">
                <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-600">
                  <tr>
                    <th className="px-4 py-3">Name</th>
                    <th className="px-4 py-3">Type</th>
                    <th className="px-4 py-3">Nullable</th>
                    <th className="px-4 py-3">Primary key</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedTable.columns.map((column) => (
                    <tr key={`${selectedTable.name}-${column.name}`} className="border-t border-gray-200">
                      <td className="px-4 py-3 font-mono text-gray-800">{column.name}</td>
                      <td className="px-4 py-3 font-mono text-gray-600">{column.type}</td>
                      <td className="px-4 py-3">{column.nullable ? 'Yes' : 'No'}</td>
                      <td className="px-4 py-3">{column.isPrimary ? 'Yes' : 'No'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <h5 className="text-lg font-semibold text-gray-800 mb-3">Foreign Keys</h5>
            {selectedTable.foreignKeys.length ? (
              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full text-left text-sm text-gray-700">
                  <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-600">
                    <tr>
                      <th className="px-4 py-3">Column</th>
                      <th className="px-4 py-3">References</th>
                      <th className="px-4 py-3">Target column</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedTable.foreignKeys.map((foreignKey) => (
                      <tr key={`${selectedTable.name}-${foreignKey.column}-${foreignKey.foreignTable}`} className="border-t border-gray-200">
                        <td className="px-4 py-3 font-mono text-gray-800">{foreignKey.column}</td>
                        <td className="px-4 py-3">{foreignKey.foreignTable}</td>
                        <td className="px-4 py-3 font-mono text-gray-600">{foreignKey.foreignColumn}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="bg-gray-50 border border-dashed border-gray-300 rounded-lg px-4 py-8 text-center text-gray-500">
                No foreign keys defined for this table.
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
};

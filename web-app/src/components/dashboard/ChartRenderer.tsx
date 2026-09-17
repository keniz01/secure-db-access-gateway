import { useMemo } from 'react';

interface ChartRendererProps {
  data: Record<string, unknown>[];
}

const MAX_CATEGORIES = 24;
const MAX_SERIES = 4;
const CHART_WIDTH = 720;
const CHART_HEIGHT = 320;
const PADDING = { top: 16, right: 16, bottom: 88, left: 56 };
const BAR_COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706'];

const isNumeric = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

interface ChartSeries {
  name: string;
  values: number[];
}

interface ChartModel {
  categories: string[];
  series: ChartSeries[];
  maxValue: number;
  truncated: boolean;
}

const buildModel = (data: Record<string, unknown>[]): ChartModel | null => {
  if (data.length === 0) return null;

  const columns = Object.keys(data[0]);
  const numericColumns = columns
    .filter((column) => data.some((row) => isNumeric(row[column])))
    .slice(0, MAX_SERIES);
  if (numericColumns.length === 0) return null;

  const categoryColumn = columns.find((column) => !numericColumns.includes(column));
  const rows = data.slice(0, MAX_CATEGORIES);
  const categories = rows.map((row, index) =>
    categoryColumn ? String(row[categoryColumn] ?? '—') : String(index + 1)
  );
  const series = numericColumns.map((name) => ({
    name,
    values: rows.map((row) => (isNumeric(row[name]) ? (row[name] as number) : 0)),
  }));
  const maxValue = Math.max(1, ...series.flatMap((entry) => entry.values));

  return { categories, series, maxValue, truncated: data.length > rows.length };
};

export const ChartRenderer = ({ data }: ChartRendererProps): React.JSX.Element => {
  const model = useMemo(() => buildModel(data), [data]);

  if (!model) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-500 text-lg">This result cannot be charted.</p>
      </div>
    );
  }

  const { categories, series, maxValue, truncated } = model;
  const plotWidth = CHART_WIDTH - PADDING.left - PADDING.right;
  const plotHeight = CHART_HEIGHT - PADDING.top - PADDING.bottom;
  const groupWidth = plotWidth / categories.length;
  const barWidth = (groupWidth * 0.7) / series.length;
  const baseline = PADDING.top + plotHeight;

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        role="img"
        aria-label="Query result chart"
        className="w-full min-w-[520px] h-auto"
      >
        <line
          x1={PADDING.left}
          y1={baseline}
          x2={PADDING.left + plotWidth}
          y2={baseline}
          stroke="#d1d5db"
          strokeWidth={1}
        />
        <line
          x1={PADDING.left}
          y1={PADDING.top}
          x2={PADDING.left}
          y2={baseline}
          stroke="#d1d5db"
          strokeWidth={1}
        />
        <text x={8} y={PADDING.top + 4} className="fill-gray-500" fontSize={11}>
          {maxValue}
        </text>
        <text x={8} y={baseline} className="fill-gray-500" fontSize={11}>
          0
        </text>

        {categories.map((category, categoryIndex) =>
          series.map((entry, seriesIndex) => {
            const value = entry.values[categoryIndex];
            const barHeight = (value / maxValue) * plotHeight;
            const x =
              PADDING.left + categoryIndex * groupWidth + groupWidth * 0.15 + seriesIndex * barWidth;
            const y = baseline - barHeight;
            return (
              <rect
                key={`${categoryIndex}-${entry.name}`}
                x={x}
                y={y}
                width={Math.max(1, barWidth - 2)}
                height={Math.max(0, barHeight)}
                fill={BAR_COLORS[seriesIndex % BAR_COLORS.length]}
                rx={2}
              >
                <title>{`${category} — ${entry.name}: ${value}`}</title>
              </rect>
            );
          })
        )}

        {categories.map((category, categoryIndex) => (
          <text
            key={categoryIndex}
            x={PADDING.left + categoryIndex * groupWidth + groupWidth / 2}
            y={baseline + 16}
            className="fill-gray-600"
            fontSize={11}
            textAnchor="end"
            transform={`rotate(-35 ${PADDING.left + categoryIndex * groupWidth + groupWidth / 2} ${baseline + 16})`}
          >
            {category.length > 14 ? `${category.slice(0, 13)}…` : category}
          </text>
        ))}

        {series.map((entry, seriesIndex) => (
          <g key={entry.name} transform={`translate(${PADDING.left + seriesIndex * 150}, ${CHART_HEIGHT - 14})`}>
            <rect
              width={10}
              height={10}
              y={-9}
              fill={BAR_COLORS[seriesIndex % BAR_COLORS.length]}
              rx={2}
            />
            <text x={16} className="fill-gray-700" fontSize={12}>
              {entry.name}
            </text>
          </g>
        ))}
      </svg>
      {truncated && (
        <p className="text-xs text-gray-500 mt-2">
          Showing the first {categories.length} categories.
        </p>
      )}
    </div>
  );
};

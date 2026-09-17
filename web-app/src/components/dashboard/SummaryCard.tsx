interface SummaryCardProps {
  content: string;
  items?: string[] | null;
}

export const SummaryCard = ({ content, items }: SummaryCardProps): React.JSX.Element => {
  const hasItems = Boolean(items && items.length > 0);

  return (
    <div className="bg-indigo-50 border-l-4 border-indigo-400 p-4 rounded-lg">
      <p className="text-xs font-semibold uppercase tracking-wide text-indigo-700 mb-1">
        AI summary
      </p>
      {hasItems ? (
        <ul className="space-y-1.5">
          {items!.map((item, idx) => (
            <li key={idx} className="text-gray-800">
              <span className="inline-block w-4 mr-2 text-indigo-600">•</span>
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-gray-800 leading-relaxed whitespace-pre-line">{content}</p>
      )}
    </div>
  );
};
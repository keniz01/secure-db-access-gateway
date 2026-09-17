import { SummaryCard } from './SummaryCard';

interface ListRendererProps {
  content: string;
}

const normalizeItems = (content: string): string[] => {
  return content
    .split(/(?:\r?\n)+/)
    .map((line) => line.trim().replace(/^(?:[-*•]|\d+[.)])\s+/, ''))
    .filter(Boolean);
};

export const ListRenderer = ({ content }: ListRendererProps): React.JSX.Element => {
  const items = normalizeItems(content);

  if (items.length <= 1) {
    return <SummaryCard content={items[0] ?? content} />;
  }

  return <SummaryCard content={content} items={items} />;
};
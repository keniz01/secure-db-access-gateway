import { SummaryCard } from './SummaryCard';

interface ParagraphRendererProps {
  content: string;
}

export const ParagraphRenderer = ({ content }: ParagraphRendererProps): React.JSX.Element => (
  <SummaryCard content={content} />
);
/** 搜索结果卡片：题型 / 题干 / 选项 / 答案 / 异常标签 / 来源文档。 */
import { Box, Button, Divider, Paper, Stack, Typography } from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import type { Highlight as HighlightPayload, Question } from '../api/client';
import Highlight from './Highlight';
import StatusChip from './StatusChip';

export interface QuestionCardProps {
  question: Question;
  highlight?: HighlightPayload;
  /** 是否显示底部「查看详情」按钮。 */
  showActions?: boolean;
}

/** 无高亮时用纯文本补齐（详情页/异常页复用同一卡片）。 */
function plainHighlight(question: Question): HighlightPayload {
  return {
    stem: question.stem,
    options: { ...question.options },
    answer: question.answer,
    analysis: question.analysis,
  };
}

export default function QuestionCard({
  question,
  highlight,
  showActions = true,
}: QuestionCardProps): JSX.Element {
  const content = highlight ?? plainHighlight(question);
  const letters = Object.keys(content.options).sort();
  const openAnomalies = question.anomalies.filter((item) => item.handled === 0);

  return (
    <Paper variant="outlined" className="p-4" sx={{ borderRadius: 2, bgcolor: '#fff' }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <StatusChip kind="qtype" value={question.qtype} />
        <Typography variant="caption" color="text.secondary">
          题号 {question.question_no}
        </Typography>
        <StatusChip kind="question_status" value={question.status} />
        {openAnomalies.length > 0 ? (
          openAnomalies.map((anomaly) => (
            <StatusChip key={`${anomaly.id}-${anomaly.type}`} kind="anomaly" value={anomaly.type} />
          ))
        ) : (
          <Typography variant="caption" color="success.main">
            无未处理异常
          </Typography>
        )}
        {question.filename ? (
          <Typography variant="caption" color="text.secondary">
            来源：{question.filename}
          </Typography>
        ) : null}
      </Stack>

      <Typography component="div" variant="subtitle1" className="mt-2" sx={{ fontWeight: 600, lineHeight: 1.6 }}>
        <Highlight html={content.stem} />
      </Typography>

      {letters.length > 0 ? (
        <div className="option-grid mt-2">
          {letters.map((letter) => (
            <Typography component="div" variant="body2" key={letter} color="text.primary" className="option-row">
              <span className="option-letter">{letter}.</span>
              <span className="option-text">
                <Highlight html={content.options[letter]} />
              </span>
            </Typography>
          ))}
        </div>
      ) : (
        <Typography variant="body2" color="text.secondary" className="mt-2">
          无选项（判断/填空类）
        </Typography>
      )}

      <Divider sx={{ my: 1.5 }} />

      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Typography variant="body2" color="success.main" sx={{ fontWeight: 600 }}>
          答案：
        </Typography>
        <Typography component="div" variant="body2" color="success.main">
          <Highlight html={content.answer || '未识别'} />
        </Typography>
      </Stack>

      {content.analysis ? (
        <Typography component="div" variant="body2" color="text.secondary" className="mt-1">
          <span className="text-gray-500 mr-1">解析：</span>
          <Highlight html={content.analysis} />
        </Typography>
      ) : null}

      {openAnomalies.length > 0 ? (
        <Box className="mt-2">
          {openAnomalies.map((anomaly) => (
            <Typography key={anomaly.id} variant="caption" color="error.main" display="block">
              · {anomaly.detail}
            </Typography>
          ))}
        </Box>
      ) : null}

      {showActions ? (
        <Box className="mt-3">
          <Button size="small" variant="outlined" component={RouterLink} to={`/questions/${question.id}`}>
            查看 / 编辑
          </Button>
        </Box>
      ) : null}
    </Paper>
  );
}

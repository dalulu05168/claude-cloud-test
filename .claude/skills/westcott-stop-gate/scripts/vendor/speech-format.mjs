import { publicWebUrl } from './news-image.mjs';

const assistantLabel = '===== 资讯助理 =====';
const teacherLabel = '===== David先生 =====';
const rolePattern = /^(={3,}\s*([^=]+?)\s*={3,})(?:\s*(\S.*))?$/;
const memberPattern = /^(\d{1,3}\s*[男女](?:[ \t]+[\p{L}][\p{L} .-]{0,30})?\s*[:：])\s*(.*)$/u;
const headlinePattern = /^(\p{Regional_Indicator}{2})[ \t]+([^｜|\n]{1,24})财经新闻｜(\S[^\n]*)$/u;
const newsLike = /^(?:\p{Regional_Indicator}{2}\s*)?[^｜|\n]{0,24}财经(?:新闻|资讯快报|快报)[｜|]/u;
const countryFlags = { '🇷🇴': '罗马尼亚', '🇺🇸': '美国' };
export const pictureFooter = /^配图[：:]\s*(\S+)$/;

export function getBlocks(body) {
  const blocks = [];
  let current;
  const lines = body.replace(/\r\n?/g, '\n').split('\n');
  for (const [index, line] of lines.entries()) {
    const trimmed = line.trim();
    const actor = rolePattern.exec(trimmed);
    const member = memberPattern.exec(trimmed);
    if (actor || member) {
      const name = actor?.[2].trim();
      let role = member ? 'member' : 'other';
      if (['资讯助理', '助理'].includes(name)) role = 'assistant';
      if (name === '先生' || /^david\s*先生$/i.test(name ?? '')) role = 'teacher';
      const inlineStaffText = actor?.[3];
      const inlineText = inlineStaffText ?? member?.[2];
      current = { role, label: actor?.[1] ?? member[1], line: index + 1,
        lines: inlineText ? [inlineText] : [], inlineStaffText };
      blocks.push(current);
    } else if (current) {
      current.lines.push(line);
    }
  }
  return blocks;
}

function splitParagraphs(lines) {
  return lines.join('\n').trim().split(/\n[ \t]*\n(?:[ \t]*\n)*/).map((chunk) => chunk.trim()).filter(Boolean);
}

function validateSpeechFormat({ body, mode = 'mixed', expected_image_urls, expected_news }) {
  const blocks = getBlocks(body);
  const issues = [];
  let newsCount = 0;
  let teacherCount = 0;

  function checkBlock(block, label, count, lines = block.lines) {
    if (block.label !== label) issues.push({ line: block.line, message: `发言标记应为「${label}」。` });
    if (block.lines[0]?.trim() !== '') {
      issues.push({ line: block.line, message: '角色标记与正文之间需要空一行。' });
    }
    const paragraphs = splitParagraphs(lines);
    if (!paragraphs.length) {
      issues.push({ line: block.line, message: '角色标记下没有正文。' });
    } else if (count !== undefined && paragraphs.length !== count) {
      issues.push({ line: block.line, message: `正文需 ${count} 段，当前为 ${paragraphs.length} 段。` });
    }
    return paragraphs;
  }

  for (const block of blocks) {
    const isNews = mode !== 'teacher' && block.role === 'assistant'
      && (mode === 'news' || block.lines.some((line) => newsLike.test(line.trim())));
    if (isNews) {
      newsCount += 1;
      const lines = block.lines.join('\n').trimEnd().split('\n');
      const footer = pictureFooter.exec(lines.at(-1)?.trim() || '');
      const image = footer && publicWebUrl(footer[1]);
      if (!image) issues.push({ line: block.line, message: '新闻下方需要一行「配图：」加可打开的事件图片直链。' });
      if (expected_image_urls && image !== publicWebUrl(expected_image_urls[newsCount - 1])) {
        issues.push({ line: block.line, message: '新闻下方的配图链接与该条已核图片不一致。' });
      }
      const [title = ''] = checkBlock(block, assistantLabel, 3, footer ? lines.slice(0, -1) : lines);
      const match = headlinePattern.exec(title);
      if (!match) {
        issues.push({ line: block.line, message: '首段用一行「国旗 国家财经新闻｜具体事件标题」。' });
      } else if (countryFlags[match[1]] && countryFlags[match[1]] !== match[2].trim()) {
        issues.push({ line: block.line, message: '标题的国旗与国家不一致。' });
      }
      const expected = expected_news?.[newsCount - 1];
      if (expected_news && (!expected || !match || match[2].trim() !== expected.country
        || match[3].trim() !== expected.title || image !== publicWebUrl(expected.image_url))) {
        issues.push({ line: block.line, message: '该条新闻的顺序、国家、标题或配图与已核条目不一致。' });
      }
    } else if (mode !== 'news' && block.role === 'teacher') {
      teacherCount += 1;
      checkBlock(block, teacherLabel);
    }

  }

  if (mode === 'news' && newsCount === 0) issues.push({ line: 1, message: '找不到资讯助理发言标记。' });
  if (expected_image_urls && expected_image_urls.length !== newsCount) {
    issues.push({ line: 1, message: '已核配图数与新闻条数不一致。' });
  }
  if (expected_news && expected_news.length !== newsCount) {
    issues.push({ line: 1, message: '成稿新闻条数与已核条目数不一致。' });
  }
  if (mode === 'teacher' && teacherCount === 0) issues.push({ line: 1, message: '找不到 David 先生发言标记。' });
  return { passed: issues.length === 0, news_count: newsCount, teacher_count: teacherCount, issues };
}

export function createSpeechFormatTool({ text, schema }) {
  const definition = {
    name: 'validate_speech_format',
    description: 'Check role labels and paragraphs. For verified news use expected_news from validate_news_broadcast to compare each headline, country, image and order.',
    inputSchema: schema({
      body: text(160000),
      mode: { type: 'string', enum: ['news', 'teacher', 'mixed'], maxLength: 15 },
      expected_image_urls: { type: 'array', items: text(2000), minItems: 0, maxItems: 40 },
      expected_news: { type: 'array', items: schema({ country: text(24), title: text(300), image_url: text(2000) },
        ['country', 'title', 'image_url']), minItems: 0, maxItems: 40 },
    }, ['body']),
    annotations: { readOnlyHint: true, openWorldHint: false },
  };
  return { definitions: [definition], handlers: { validate_speech_format: validateSpeechFormat } };
}

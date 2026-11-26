import { getBlocks } from './speech-format.mjs';

const chineseCliches = [
  '兄弟们', '家人们', '各位老铁', '老师辛苦了', '太到位了', '干货满满', '受益匪浅', '学到了', '学到啦',
  '我悟了', '老师说得太好了', '收获满满', '格局打开', '这个逻辑没毛病', '逻辑拉满', '闭眼冲', '冲冲冲',
  '坐等起飞', '稳稳的幸福', '泼天富贵',
];
const chineseMarketSlang = [
  '割韭菜', '韭菜', '接盘侠', '庄家控盘', '庄股', '妖股', '主力洗盘', '洗盘', '护盘', '吸筹', '拉盘', '砸盘',
  '拉爆', '抬轿', '大肉', '吃肉', '吃面', '挂山顶', '上车', '下车', '梭哈', '抄底', '抄作业',
  '回血', '躺赢', '打板', '连板', '闷杀', '追高', '天地板', '地天板', '砸开',
];
const hype = /(?<![\p{L}\p{N}])(?:yyds|666|to the moon|all[- ]in|hodl|diamond hands|paper hands|pump(?: and| &) dump|următoarea rachetă|profit garantat|bani siguri)(?![\p{L}\p{N}])/iu;
const pictographic = /[\p{Extended_Pictographic}\p{Regional_Indicator}\p{Emoji_Presentation}]/u;
const graphemes = new Intl.Segmenter('ro', { granularity: 'grapheme' });
const punctuation = new Intl.Segmenter('ro', { granularity: 'sentence' });
const han = /\p{Script=Han}/u;
const foreignSentence = /[\p{L}]+(?:['’\-][\p{L}]+)*/gu;
const normalize = (body) => body.normalize('NFKC').replace(/[\u200b-\u200d\u2060\ufeff]/g, '')
  .replace(/(?<=\p{Script=Han})\s+(?=\p{Script=Han})/gu, '');

function inspectVoice({ body }) {
  const segments = getBlocks(body);
  const blocks = segments.length ? segments : [{ line: 1, label: '', role: 'other', lines: [body] }];
  const issues = [];
  let checked = 0;
  let members = 0;
  function issue(block, severity, type, message, term) {
    issues.push({ line: block.line, role: block.label || block.role, severity, type, message, ...(term ? { term } : {}) });
  }

  for (const block of blocks) {
    const message = block.lines.filter((line) => !/^\s*配图[：:]/u.test(line)).join('\n').trim();
    if (!message) continue;
    checked += 1;
    const normalized = normalize(message);
    for (const [type, terms, label] of [
      ['chinese_cliche', chineseCliches, '中文群聊套话'],
      ['market_slang', chineseMarketSlang, '股票黑话'],
    ]) {
      const hits = terms.filter((term) => normalized.includes(term));
      const distinct = hits.filter((term) => !hits.some((other) => other !== term && other.includes(term)));
      for (const term of distinct) issue(block, 'block', type, `请用本场可核实的具体表达替换${label}。`, term);
    }
    const catchphrase = hype.exec(normalized);
    if (catchphrase) issue(block, 'block', 'hype', '请用具体的事实、条件或观察代替炒作和收益口号。', catchphrase[0]);
    const words = message.match(foreignSentence) ?? [];
    const looksLikeForeignSpeech = words.length >= 3 || (words.length === 2 && words.some((word) => /^\p{Ll}+$/u.test(word)))
      || /^(?:păi|deci|mda)[.!?…]*$/iu.test(message);
    if (!han.test(message) && looksLikeForeignSpeech) {
      issue(block, 'block', 'language', '所有角色发言用中文。人名、代码及必要的专业缩写可保留。');
    }
    if (block.role !== 'member') continue;
    members += 1;
    const emojis = [...graphemes.segment(message)].filter(({ segment }) => pictographic.test(segment));
    if (emojis.length > 1) issue(block, 'review', 'emoji_density', '会员短句一般用零或一个自然表情，检查多余的表情。');
    if (emojis.some(({ segment }) => /[🚀💰🤑]/u.test(segment))) {
      issue(block, 'review', 'hype_emoji', '检查这里的表情是否给价量观察加上了不必要的炒作意味。');
    }
    const sentences = [...punctuation.segment(message)].filter(({ segment }) => /[\p{L}\p{N}]/u.test(segment));
    const length = (message.match(/\p{Script=Han}/gu)?.length ?? 0) + (message.match(/\b[\p{Script=Latin}\d]+\b/gu)?.length ?? 0);
    if (sentences.length > 2 || length > 42) {
      issue(block, 'review', 'member_length', '成员通常只表达一个问题或观察，检查能否用一到两句说完。');
    }
  }
  return { passed: !issues.some((entry) => entry.severity === 'block'), checked_messages: checked,
    member_messages: members, needs_review: issues.some((entry) => entry.severity === 'review'), issues };
}

export function createWhatsAppVoiceTool({ text, schema }) {
  const definition = {
    name: 'validate_whatsapp_voice',
    description: 'Check Chinese dialogue in Romanian financial WhatsApp style for clichés, stock slang, non-Chinese sentences and member message length or emoji density.',
    inputSchema: schema({ body: text(160000) }, ['body']),
    annotations: { readOnlyHint: true, openWorldHint: false },
  };
  return { definitions: [definition], handlers: { validate_whatsapp_voice: inspectVoice } };
}

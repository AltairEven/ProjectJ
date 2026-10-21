import assert from 'node:assert/strict'
import test from 'node:test'
import { describe, discussionLines, evaluationLines, modelLines, resourceLines, revisionChanges } from '../src/report.ts'

const doc = (object_id, revision, kind, body) => ({ object_id, revision, kind, body })
const docs = [
  doc('c', 1, 'course', { parent_preview: { story: 'Ben’s Bridge（新材料）' } }),
  doc('s', 2, 'state', { dimensions: [{ dimension_id: 'english.reading.facts', judgment: 'needs_help' }] }),
  doc('cap', 1, 'capability', { dimension_id: 'english.reading.facts', name: '阅读事实与顺序' }),
  doc('p', 2, 'plan', { main_goal: { description: '旧目标' } }),
]

test('next-course results read as labelled Chinese rows with what each saved object says', () => {
  const rows = new Map(describe({
    next_package: { course_ref: { object_id: 'c', revision: 1 }, start: 'pending', unverified: ['actual_api', 'voice'] },
    adoption: { state_ref: { object_id: 's', revision: 2 }, plan_ref: { object_id: 'p', revision: 1 }, plan_effect: 'reused_with_reason' },
  }, docs))
  assert.equal(rows.get('课程'), '修订1：Ben’s Bridge（新材料）')
  assert.equal(rows.get('开始'), '待开始')
  assert.equal(rows.get('未验证'), '实际API、声音')
  assert.equal(rows.get('学习状态'), '修订2：阅读事实与顺序：需要帮助')
  assert.equal(rows.get('计划'), '修订1（当前已是修订2）')
  assert.equal(rows.get('计划处理'), '沿用并说明理由')
})

test('a fast tier binding and its probe item read as Fast', () => {
  const lines = modelLines({
    initialized: true, simulated: false,
    purposes: {
      classroom_judgment: { binding: { adapter: 'codex', model: 'gpt-6.1-sol', effort: 'low', tier: 'fast' }, reasons: [] },
      evaluation: { binding: { adapter: 'codex', model: 'gpt-6.1-sol', effort: 'medium', tier: 'default' }, reasons: [] },
    },
    reports: [{ adapter: 'codex', model: 'gpt-6.1-sol', items: { 'tier:fast': { status: 'unverified' } } }],
  })
  assert.deepEqual(lines, [
    '模型已初始化。',
    '课堂判断：codex · gpt-6.1-sol · low · Fast',
    '课后评价：codex · gpt-6.1-sol · medium',
    '能力报告 codex · gpt-6.1-sol：未核实 Fast层级',
  ])
})

test('model status reads as Chinese lines without any credential field', () => {
  const ok = modelLines({
    initialized: true, simulated: true,
    purposes: {
      planning: { binding: { adapter: 'simulated', model: 'simulated-jane', effort: 'medium' }, reasons: [] },
      preparation: { binding: { adapter: 'simulated', model: 'simulated-jane', effort: 'medium' }, reasons: [] },
      classroom_judgment: { binding: { adapter: 'simulated', model: 'simulated-jane', effort: 'low' }, reasons: [] },
      evaluation: { binding: { adapter: 'simulated', model: 'simulated-jane', effort: 'medium' }, reasons: [] },
    },
    reports: [{ adapter: 'simulated', model: 'simulated-jane', items: {
      auth: { status: 'verified' }, model: { status: 'verified' }, structured_output: { status: 'verified' },
      cancellation: { status: 'verified' }, 'effort:low': { status: 'verified' }, 'effort:medium': { status: 'verified' },
      tools: { status: 'verified' }, image_input: { status: 'unsupported' }, audio: { status: 'not_probed' } } }],
  })
  assert.deepEqual(ok, [
    '模型已初始化（模拟适配器）。',
    '学习规划：simulated · simulated-jane · medium',
    '备课：simulated · simulated-jane · medium',
    '课堂判断：simulated · simulated-jane · low',
    '课后评价：simulated · simulated-jane · medium',
    '能力报告 simulated · simulated-jane：已核实 鉴权、模型、结构化输出、取消、档位low、档位medium、工具关闭；不支持 图片输入；未探测 音频',
  ])
  const blocked = modelLines({
    initialized: false, simulated: false,
    purposes: { evaluation: { binding: null, reasons: ['未配置默认模型'] } },
  })
  assert.equal(blocked[0], '模型未初始化，备课、开课、复盘和评价暂不可用。')
  assert.equal(blocked[1], '课后评价：未配置；未通过：未配置默认模型')
  assert.deepEqual(modelLines(undefined), ['模型接入状态未取得。'])
})

test('an evaluation reads as summary, evidence, unknowns, differences and per-task lines', () => {
  const evaluation = doc('e', 1, 'evaluation', {
    report: { summary: '完成两题。', strongest: ['首题初答对'], uncertain: [], alignment: '离屏未做。' },
    tasks: [{ task_id: 'reading-fact', dimension_id: 'english.reading.facts', working_judgment: 'single_independent', explanation: '初答正确，学生说没有求助。' }],
    checks: [{ attempt_id: 'a2', task_id: 'reading-sequence', reason: '单次独立的条件不足' }],
  })
  const rows = evaluationLines(evaluation, docs)
  assert.deepEqual(rows[0], ['本课概括', '完成两题。'])
  assert.deepEqual(rows[1], ['最有依据', '首题初答对'])
  assert.deepEqual(rows[2], ['尚未确定', '无'])
  assert.deepEqual(rows[3], ['课程与课堂差异', '离屏未做。'])
  assert.deepEqual(rows[4], ['阅读事实与顺序·reading-fact', '单次独立：初答正确，学生说没有求助。'])
  assert.deepEqual(rows.at(-1), ['规则调整', 'reading-sequence：单次独立的条件不足'])
})

test('a rewritten evaluation shows the replaced summary and each judgment that changed', () => {
  const task = (task_id, working_judgment) => ({ task_id, dimension_id: 'english.reading.facts', working_judgment })
  const before = doc('e', 1, 'evaluation', { report: { summary: '两题都独立完成。' }, tasks: [task('reading-fact', 'single_independent'), task('reading-sequence', 'needs_help')] })
  const after = doc('e', 2, 'evaluation', { report: { summary: '首题看了答案。' }, tasks: [task('reading-fact', 'unknown'), task('reading-sequence', 'needs_help')] })
  assert.deepEqual(revisionChanges(after, before, docs), [
    ['上一版概括', '两题都独立完成。'],
    ['阅读事实与顺序·reading-fact', '单次独立 → 未知'],
  ])
  assert.deepEqual(revisionChanges(before, before, docs).at(-1), ['逐题判断', '没有变化'])
  assert.deepEqual(revisionChanges(after, doc('e', 1, 'evaluation', { tasks: [] }), docs)[0], ['上一版概括', '上一版没有模型解读。'])
})

test('an evaluation saved before model drafts says so', () => {
  assert.deepEqual(evaluationLines(doc('e', 1, 'evaluation', { tasks: [] }), docs), [['课后评价', '这一版评价没有模型解读。']])
})

test('a restored run is labelled without saying simulation', () => {
  const rows = new Map(describe({ result: 'restored_to_new_simulation', run_id: 'restored-1' }, docs))
  assert.equal(rows.get('结果'), '已恢复到新运行目录')
  assert.equal(rows.get('新运行目录'), 'restored-1')
})

test('a model-planned demand explains its change and an uncovered goal reads as not ready', () => {
  const planned = [...docs,
    doc('cap2', 1, 'capability', { dimension_id: 'science.observation.report', name: '实际观察报告' }),
    doc('d', 3, 'demand', {
      direction: 'observe', primary_goal: { dimension_id: 'science.observation.report', description: '说出纸桥的实际条件与结果' },
      note: { before: '阅读事实', after: '实际观察报告', why: '阅读两题都需要提示', uncertain: ['保持', '迁移'], expectation: '先报告实际条件' },
    }),
    doc('r', 1, 'review', { conclusion: '先补实际报告', unknown: ['可用时间'], checks: ['预算固定30分钟，改动不生效。'] }),
  ]
  const rows = new Map(describe({
    next_package: { status: 'not_ready', reason: '课程未就绪：现有固定课程只覆盖英语阅读事实与英语短报告。' },
    adoption: { demand_ref: { object_id: 'd', revision: 3 }, plan_effect: 'adjusted', plan_reason: '本周先补样本' },
    review: { review_ref: { object_id: 'r', revision: 1 } },
  }, planned))
  assert.equal(rows.get('下一课需求'), '修订3：短观察：说出纸桥的实际条件与结果')
  assert.equal(rows.get('原安排'), '阅读事实')
  assert.equal(rows.get('新安排'), '实际观察报告')
  assert.equal(rows.get('为什么'), '阅读两题都需要提示')
  assert.equal(rows.get('还不确定'), '保持；迁移')
  assert.equal(rows.get('下节预期'), '先报告实际条件')
  assert.equal(rows.get('课程状态'), '课程未就绪')
  assert.match(rows.get('原因'), /^课程未就绪：现有固定课程/)
  assert.equal(rows.get('计划处理'), '已调整')
  assert.equal(rows.get('计划理由'), '本周先补样本')
  assert.equal(rows.get('仍未知'), '可用时间')
  assert.equal(rows.get('规则调整'), '预算固定30分钟，改动不生效。')
})

test('resource lines show stage, spoken words, versions and adoption', () => {
  const demo = { resource_id: 'demo:read-aloud-1', kind: 'audio', purpose: '朗读示范：read-aloud-1', text: 'Mia has paper.', required: true,
    stage: 'help', fallback: '采用后才能开课。', state: 'generated', versions: [{ version: 1 }, { version: 2 }], adopted_version: 1 }
  const lines = resourceLines({ resources: [
    demo,
    { resource_id: 'cup', kind: 'image', purpose: '展示两个杯子', required: false, stage: 'help', fallback: '拿出实物杯子。', state: 'planned' },
    { resource_id: 'clip', kind: 'video', purpose: '倒水', required: false, stage: 'question', fallback: '自己演示。', state: 'not_connected' },
    { resource_id: 'voice:goal', kind: 'audio', purpose: '开场说明', text: '我是Jane。', required: false, stage: 'question', fallback: '现场合成。',
      state: 'adopted', versions: [{ version: 1 }], adopted_version: 1 },
  ] })
  assert.deepEqual(lines, [
    '语音（必需，求助后）：朗读示范：read-aloud-1「Mia has paper.」；没有时：采用后才能开课。——待查看，采用第1版，共2版',
    '图片（可选，求助后）：展示两个杯子；没有时：拿出实物杯子。——已列入清单，确认后生成',
    '视频（可选，提问时）：倒水；没有时：自己演示。——未接入',
    '语音（可选，提问时）：开场说明「我是Jane。」；没有时：现场合成。——已采用，采用第1版，共1版',
  ])
  assert.deepEqual(resourceLines({ resources: [] }), [])
})

test('discussion rounds read as the student feedback and what the model changed', () => {
  assert.deepEqual(discussionLines({ discussion: [{ feedback: '第一题再简单一点。', note: '第一题改得更直接。' }], confirmation: null }), [
    ['第1轮意见', '第一题再简单一点。'],
    ['第1轮修改说明', '第一题改得更直接。'],
    ['确认', '待确认'],
  ])
  assert.deepEqual(discussionLines({ discussion: [], confirmation: { state: 'confirmed', confirmed_at: '2026-10-19T12:00:00+00:00' } }), [
    ['确认', '已确认（2026-10-19T12:00:00+00:00）'],
  ])
})

test('a drafted course in the next-course report reads as waiting for confirmation', () => {
  assert.equal(new Map(describe({ next_package: { preparation: 'drafted' } }, docs)).get('备课'), '已编写，待确认')
})

import assert from 'node:assert/strict'
import test from 'node:test'
import { describe, modelLines, startMessage } from '../src/report.ts'

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

test('a start without a classroom says what is missing or failed in Chinese', () => {
  assert.equal(startMessage({ decision: 'waiting', missing: ['materials', 'accompaniment'] }), '尚不能开始，待确认：材料、陪伴。')
  assert.match(startMessage({ decision: 'blocked', missing: [], reason: 'required household condition failed' }), /本次不开课/)
  assert.equal(startMessage({ decision: 'blocked', reason: 'new reason' }), '尚不能开始（new reason）。')
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

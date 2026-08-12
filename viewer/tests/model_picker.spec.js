// ModelPicker component tests — TB-mode test list ordering + labels.
//
// In TB mode the picker lists tests. Each option reads
// ``dut — tb_top — test``: the model/DUT first (so every test for a
// design clusters — a tb name like ``tb_top`` is reused across DUTs),
// then the testbench top, then the test name. The list is sorted by the
// same key. The option *value* is the (tests_file, name) composite
// because a test name can repeat across suites, and switching sends the
// tests_file so the hub resolves it unambiguously.

import { describe, expect, it, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import ModelPicker from '../src/components/ModelPicker.vue'
import { useViewerStore } from '../src/store.js'

// Collapse runs of whitespace so the assertions don't hinge on the
// exact spacing around the separator.
const norm = (s) => s.replace(/\s+/g, ' ').trim()

function tbOptionTexts(wrapper) {
  return wrapper
    .findAll('option')
    .filter((o) => o.attributes('value')) // drop the empty "(select a test)" placeholder
    .map((o) => norm(o.text()))
}

describe('ModelPicker — TB-mode test list', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('labels read dut — tb_top — test, grouped by dut then tb', () => {
    const store = useViewerStore()
    // Two DUTs share the tb name ``tb_top``; a third DUT stands alone.
    store.availableTests = [
      { name: 'basic', model: 'beta_dut', tb: 'tb_top', tests_file: 'b' },
      { name: 'smoke', model: 'apb_intf', tb: 'tb_apb', tests_file: 'a' },
      { name: 'basic', model: 'alpha_dut', tb: 'tb_top', tests_file: 'c' },
      { name: 'aaa', model: 'beta_dut', tb: 'tb_top', tests_file: 'b' },
    ]
    store.activeTest = 'smoke'
    store.activeTestFile = 'a' // hide the placeholder
    store.viewModeTb = true

    const wrapper = mount(ModelPicker)
    expect(tbOptionTexts(wrapper)).toEqual([
      // sorted by model (DUT), then tb, then test name
      'alpha_dut — tb_top — basic',
      'apb_intf — tb_apb — smoke',
      'beta_dut — tb_top — aaa',
      'beta_dut — tb_top — basic',
    ])
  })

  it('option values are the (tests_file, name) composite — unique for dup names', () => {
    const store = useViewerStore()
    store.availableTests = [
      { name: 'smoke', model: 'apb_intf', tb: 'tb_apb', tests_file: '/p/apb/tests.yaml' },
      { name: 'smoke', model: 'ip_cdc', tb: 'tb_cdc', tests_file: '/p/cdc/tests.yaml' },
    ]
    store.activeTest = 'smoke'
    store.activeTestFile = '/p/apb/tests.yaml'
    store.viewModeTb = true

    const wrapper = mount(ModelPicker)
    const values = wrapper
      .findAll('option')
      .map((o) => o.attributes('value'))
      .filter(Boolean)
    expect(values).toEqual([
      '/p/apb/tests.yaml::smoke',
      '/p/cdc/tests.yaml::smoke',
    ])
  })

  it('selecting an option calls switchTest with the name and its tests_file', async () => {
    const store = useViewerStore()
    store.availableTests = [
      { name: 'smoke', model: 'apb_intf', tb: 'tb_apb', tests_file: '/p/apb/tests.yaml' },
      { name: 'smoke', model: 'ip_cdc', tb: 'tb_cdc', tests_file: '/p/cdc/tests.yaml' },
    ]
    store.activeTest = 'smoke'
    store.activeTestFile = '/p/apb/tests.yaml'
    store.viewModeTb = true
    const spy = vi.spyOn(store, 'switchTest').mockResolvedValue(true)

    const wrapper = mount(ModelPicker)
    await wrapper.find('select').setValue('/p/cdc/tests.yaml::smoke')

    expect(spy).toHaveBeenCalledWith('smoke', {
      updateUrl: true,
      testsFile: '/p/cdc/tests.yaml',
    })
  })

  it('does not mutate the store array when sorting', () => {
    const store = useViewerStore()
    const input = [
      { name: 'b', model: 'm', tb: 'z_tb', tests_file: 'x' },
      { name: 'a', model: 'm', tb: 'a_tb', tests_file: 'x' },
    ]
    store.availableTests = input
    store.activeTest = 'a'
    store.activeTestFile = 'x'
    store.viewModeTb = true

    mount(ModelPicker)
    expect(store.availableTests.map((t) => t.name)).toEqual(['b', 'a'])
  })

  it('falls back gracefully when a test has no tb/model', () => {
    const store = useViewerStore()
    store.availableTests = [{ name: 'lone', model: '', tb: '', tests_file: 'x' }]
    store.activeTest = 'lone'
    store.activeTestFile = 'x'
    store.viewModeTb = true

    const wrapper = mount(ModelPicker)
    expect(tbOptionTexts(wrapper)).toEqual(['lone'])
  })
})

describe('ModelPicker — DUT-mode view_status badges (#130)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function optionsOf(wrapper) {
    return wrapper
      .findAll('option')
      .filter((o) => o.attributes('value'))
  }

  it('badges failed and never_built models, leaves ok ones clean', () => {
    const store = useViewerStore()
    store.availableModels = [
      { name: 'good', models_file: 'm', view_status: 'ok' },
      { name: 'apb_intf', models_file: 'm', view_status: 'failed',
        error: 'rtl-buddy-view exited with code 1' },
      { name: 'fresh', models_file: 'm', view_status: 'never_built' },
      // Pre-#130 hub: no view_status at all.
      { name: 'legacy', models_file: 'm', has_cdc: true },
    ]
    store.activeModel = 'good'

    const wrapper = mount(ModelPicker)
    const opts = optionsOf(wrapper)
    expect(opts.map((o) => norm(o.text()))).toEqual([
      'good',
      'apb_intf ⚠',
      'fresh ⚠',
      'legacy ⚡',
    ])
  })

  it("the failed option's tooltip carries the hub's error one-liner", () => {
    const store = useViewerStore()
    store.availableModels = [
      { name: 'apb_intf', models_file: 'm', view_status: 'failed',
        error: "cannot resolve interface port 'apb'" },
      { name: 'fresh', models_file: 'm', view_status: 'never_built' },
    ]
    store.activeModel = 'apb_intf'

    const wrapper = mount(ModelPicker)
    const [failed, never] = optionsOf(wrapper)
    expect(failed.attributes('title')).toBe(
      "view generation failed — cannot resolve interface port 'apb'",
    )
    expect(failed.attributes('data-view-status')).toBe('failed')
    expect(never.attributes('title')).toContain('never built')
  })
})

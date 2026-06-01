// ModelPicker component tests — TB-mode test list ordering + labels.
//
// In TB mode the picker lists tests. Each option reads
// ``tb — model — test``: testbench top first (so same-tb tests cluster),
// then the model/DUT (a tb name like ``tb_top`` is reused across DUTs),
// then the test name. The list is sorted by the same key. The option
// *value* is the (tests_file, name) composite because a test name can
// repeat across suites, and switching sends the tests_file so the hub
// resolves it unambiguously.

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

  it('labels read tb — model — test, grouped by tb then model', () => {
    const store = useViewerStore()
    // Two DUTs share the tb name ``tb_top``; a third tb stands alone.
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
      'tb_apb — apb_intf — smoke',
      // tb_top: alpha_dut sorts before beta_dut; within beta_dut, aaa < basic
      'tb_top — alpha_dut — basic',
      'tb_top — beta_dut — aaa',
      'tb_top — beta_dut — basic',
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

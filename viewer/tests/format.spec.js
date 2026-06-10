import { describe, it, expect } from 'vitest'
import { formatBandwidth } from '../src/format.js'

describe('formatBandwidth', () => {
  it('returns 0 B/s for falsy / non-positive input', () => {
    expect(formatBandwidth(0)).toBe('0 B/s')
    expect(formatBandwidth(undefined)).toBe('0 B/s')
    expect(formatBandwidth(-5)).toBe('0 B/s')
  })

  it('converts bits/s -> bytes/s (÷8)', () => {
    expect(formatBandwidth(8)).toBe('1.00 B/s')
    expect(formatBandwidth(80)).toBe('10.00 B/s')
  })

  it('scales with DECIMAL (SI) prefixes, not binary', () => {
    // 8e6 bit/s = 1e6 B/s = 1.00 MB/s decimal (would be 0.95 MiB/s binary)
    expect(formatBandwidth(8e6)).toBe('1.00 MB/s')
    expect(formatBandwidth(8e9)).toBe('1.00 GB/s')
    expect(formatBandwidth(8e3)).toBe('1.00 kB/s')
    expect(formatBandwidth(8e12)).toBe('1.00 TB/s')
  })

  it('matches the AXI demo overlay numbers (read_bps bits/s)', () => {
    // out0 read_bps 2196699670 bit/s -> 274.59 MB/s decimal
    expect(formatBandwidth(2196699670)).toBe('274.59 MB/s')
  })
})

import { describe, expect, it } from 'vitest'
import { vec3 } from '../../../src/tools/see/pose.js'

describe('blender_see presence pose vectors', () => {
  it('parses comma-separated coordinates', () => {
    expect(vec3('6,-6,4')).toEqual([6, -6, 4])
    expect(vec3(' 1 , 2 , 3 ')).toEqual([1, 2, 3])
  })

  it('accepts numeric arrays from a pose payload', () => {
    expect(vec3([6, -6, 4])).toEqual([6, -6, 4])
    expect(vec3([-3.5, 0, 2])).toEqual([-3.5, 0, 2])
  })

  it('rejects anything that is not three finite numbers', () => {
    expect(vec3('6,-6')).toBeUndefined()
    expect(vec3('a,b,c')).toBeUndefined()
    expect(vec3('1,,3')).toBeUndefined()
    expect(vec3([1, 2])).toBeUndefined()
    expect(vec3(null)).toBeUndefined()
    expect(vec3({ x: 1, y: 2, z: 3 })).toBeUndefined()
  })
})

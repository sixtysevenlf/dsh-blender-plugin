/**
 * Presence pose normalisation for blender_see.
 *
 * The tool surface takes camera coordinates as "x,y,z" strings; the add-on
 * draws frusta from numeric triples. One parser keeps the /view request and the
 * published view pose consistent.
 */

export type Vec3 = [number, number, number]

/** Parse [x, y, z] or "x,y,z" into three finite numbers; undefined when invalid. */
export function vec3(value: unknown): Vec3 | undefined {
  const parts = typeof value === 'string' ? value.split(',') : Array.isArray(value) ? value : null
  if (!parts || parts.length !== 3) return undefined
  const nums = parts.map((part) =>
    typeof part === 'string' && part.trim() === '' ? Number.NaN : Number(part),
  )
  if (!nums.every((num) => Number.isFinite(num))) return undefined
  return [nums[0], nums[1], nums[2]]
}

// EXT 前缀路由 + gui/render/contract 兜底（由 config/routing.json 生成表驱动）
import type { PlanCallDeps } from './index.js'
import { EXT_ROUTES } from './routes.generated.js'
import type { RouteDecision } from './plan-call.js'
import { handleRoute, hit, miss, routeMatches } from './plan-call.js'

/** EXT 表按 config/routing.json 的顺序匹配：21 个模块前缀 → gui_ → render_ → contract 兜底。 */
export async function routeExtRouting(
  E: PlanCallDeps,
  o: string,
  payload: unknown,
): Promise<RouteDecision> {
  for (const route of EXT_ROUTES) {
    if (!routeMatches(route, o)) continue
    return hit(handleRoute(E, route, o, payload))
  }
  return miss()
}

# Spec Delta

## Purpose

定义 Gateway 作为唯一服务端、addon 反向连接的连接契约，使多实例 Blender 能被稳定发现、路由和重连，并拒绝旧拓扑。

## MODIFIED Requirements

### Requirement: Reconnect replaces a stale connection

Gateway SHALL mark an instance offline when its control connection closes. A later handshake with the same `instanceId` MAY replace a stale registration after validation, and requests in flight on the old connection MUST fail rather than being silently replayed. A connection that is still answering heartbeats MUST NOT be replaced.

#### Scenario: A live instance is not taken over
- **WHEN** a second connection handshakes with the `instanceId` of a connection that is still answering heartbeats
- **THEN** the newcomer is closed with `instance_in_use`, the first connection keeps its epoch and routing, and no request is lost

#### Scenario: A browser page cannot upgrade
- **WHEN** a WebSocket upgrade to `/addon` carries an `Origin` that is not loopback or `dsh-app://app`
- **THEN** Gateway refuses the upgrade and no addon channel is created

#### Scenario: Addon reconnects after a network drop
- **WHEN** an addon loses its connection and reconnects with the same instanceId
- **THEN** Gateway restores routing to the new connection and does not duplicate the instance

#### Scenario: In-flight request is not replayed
- **WHEN** the connection drops while a request is in flight
- **THEN** the caller receives an error identifying the disconnected instance and the request is not executed twice

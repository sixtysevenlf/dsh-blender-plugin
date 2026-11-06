# Spec Delta

## Purpose

定义 Gateway 作为唯一服务端、addon 反向连接的连接契约，使多实例 Blender 能被稳定发现、路由和重连，并拒绝旧拓扑。

## ADDED Requirements

### Requirement: Gateway is the only server

Gateway SHALL be the only server in the 3.1 topology. Host and tools SHALL connect only to Gateway, and the addon SHALL initiate its control connection to Gateway. The addon MUST NOT listen on 9876 or any other inbound port.

#### Scenario: Addon starts without an inbound listener
- **WHEN** a Blender addon instance starts
- **THEN** it opens an outbound connection to Gateway and creates no local listener

#### Scenario: Retired addon listener is absent
- **WHEN** Gateway is running
- **THEN** no process is listening on the retired addon port 9876

### Requirement: Gateway fails closed on port conflict

Gateway SHALL bind only its configured loopback port. If that port is already in use, Gateway MUST refuse to start, MUST NOT share the socket with the existing listener, and MUST report the port and the occupying process identity when it can be determined.

#### Scenario: Occupied port blocks startup
- **WHEN** another process already listens on the configured Gateway port
- **THEN** Gateway exits with a structured error naming the port and the occupying process and starts no listener

#### Scenario: Conflict is not reported as an addon failure
- **WHEN** Gateway cannot bind its port
- **THEN** the failure reports `stage=config` with the port and owner, and does not claim the addon is missing or unresponsive

### Requirement: Addon handshake identifies an instance

On connect, the addon SHALL send a handshake containing `instanceId`, `kind`, `version`, `protocol`, `project`, and `capabilities`. Gateway SHALL reject a handshake with a missing field or an unsupported protocol version.

#### Scenario: Valid handshake is registered
- **WHEN** an addon sends a complete handshake with protocol 12
- **THEN** Gateway registers the instance and makes it routable

#### Scenario: Protocol mismatch is rejected
- **WHEN** an addon sends protocol 11
- **THEN** Gateway rejects the connection with a structured protocol mismatch error and never routes requests to it

### Requirement: Gateway routes by instance identity

Gateway SHALL route a request to the addon instance identified by `instanceId`. If multiple instances are connected, an operation without an explicit instance MUST fail with an ambiguity error instead of choosing one silently.

#### Scenario: Two instances are routed independently
- **WHEN** two addons with different instanceIds are connected
- **THEN** a request targeting one instance is delivered only to that instance

#### Scenario: Ambiguous target is rejected
- **WHEN** two instances are connected and a session operation omits the instance
- **THEN** Gateway returns a structured ambiguity error listing the available instanceIds

### Requirement: Reconnect replaces a stale connection

Gateway SHALL mark an instance offline when its control connection closes. A later handshake with the same `instanceId` SHALL replace the stale registration after validation, and requests in flight on the old connection MUST fail rather than being silently replayed.

#### Scenario: Addon reconnects after a network drop
- **WHEN** an addon loses its connection and reconnects with the same instanceId
- **THEN** Gateway restores routing to the new connection and does not duplicate the instance

#### Scenario: In-flight request is not replayed
- **WHEN** the connection drops while a request is in flight
- **THEN** the caller receives an error identifying the disconnected instance and the request is not executed twice

### Requirement: Legacy addons are rejected

Gateway SHALL reject legacy addons that use protocol 11 or the 9876 inbound model. A mixed 3.0.x and 3.1 deployment MUST NOT be supported.

#### Scenario: Legacy addon cannot attach
- **WHEN** a 3.0.x addon attempts to connect
- **THEN** Gateway rejects it with an actionable upgrade message and does not fall back to the old topology
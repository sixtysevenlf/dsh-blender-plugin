# Blender gateway (routing delta)

## MODIFIED Requirements

### Requirement: Gateway routes by instance identity

Gateway SHALL route a request to the addon instance identified by `instanceId`. If multiple instances are connected, an operation without an explicit instance MUST fail with an ambiguity error instead of choosing one silently. Work execution, view capture, frame capture, and other operations bound to a user-gui session SHALL carry that session's instance identity from the host through the HTTP route and engine to the transport, so they do not become ambiguous while the session remains attached.

#### Scenario: Two instances are routed independently
- **WHEN** two addons with different instanceIds are connected
- **THEN** a request targeting one instance is delivered only to that instance

#### Scenario: Ambiguous target is rejected
- **WHEN** two instances are connected and an operation omits the instance
- **THEN** Gateway returns a structured ambiguity error listing the available instanceIds

#### Scenario: Session-bound operations use the session instance
- **WHEN** two instances are connected and a request is bound to a session attached to one of them
- **THEN** execution, view, and frame requests are delivered to that session's instance without an ambiguity error

#### Scenario: Missing session instance still fails closed
- **WHEN** a session-bound request has no resolvable instance and more than one instance is connected
- **THEN** Gateway returns a structured ambiguity error and does not guess

## ADDED Requirements

### Requirement: Injected runtime modules are tracked per instance

When Gateway injects a runtime module into an addon instance, it SHALL remember the injection for that instance only. A module already present in one instance MUST NOT cause injection to be skipped for another instance.

#### Scenario: Second instance receives its own injection
- **WHEN** a view module is injected into instance A and a view request targets instance B
- **THEN** Gateway injects the module into instance B before serving the request

#### Scenario: Repeat request does not re-inject
- **WHEN** two consecutive view requests target the same instance after a successful injection
- **THEN** the module is not injected a second time for that instance

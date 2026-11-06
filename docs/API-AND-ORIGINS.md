# API and data origins, in plain language

The **API** button opens the application's interactive programming reference,
usually at `http://127.0.0.1:8001/docs`. API means Application Programming
Interface: scripts and other programs can submit predictions, list weather,
check jobs and retrieve exports without clicking through the map interface.
Normal interactive use does not require it.

**OAS 3.1** means OpenAPI Specification version 3.1, the standard used to describe
that interface. It is separate from the BalloonPredictor software version.

The default documentation colours distinguish HTTP operations:

| Control | Meaning here | Example |
|---|---|---|
| Blue GET | Retrieve information or a generated file | Read weather coverage, job status or a completed export |
| Green POST | Submit settings or start an operation | Calculate a flight, prepare a sonde or download weather |

Expand an operation to read its inputs. **Try it out → Execute** sends a real
request to the running server. It is useful for learning/testing and automation;
it is not just a static screenshot of an example.

**Data origins** means where the data came from and how it was processed. For
example, an IFS track may identify its forecast cycle, valid times, source files,
height calculation and interpolation/near-surface assumptions. This helps you
check the result and reproduce it. The source trail is also included in exports.
Older releases called this provenance. Machine-readable metadata retains the
existing `provenance` keys for compatibility; the interface uses Data origins.

References:

- https://fastapi.tiangolo.com/tutorial/first-steps/
- https://spec.openapis.org/oas/v3.1.0.html
- https://swagger.io/docs/specification/v3_0/paths-and-operations/

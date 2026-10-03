# JSCAD

Create parametric 2D/3D models with JavaScript geometry operations: primitives, Boolean combinations, transformations and extrusions. Adjust parameters in the upstream web interface and export geometry, including STL for a separate printing/slicing workflow. This differs from mesh painting or scene animation tools: the editable artifact is the program that generates the shape.

## Distribution

MIT JSCAD web package 2.6.13, built from exact commit `f245ea3a5072024b789f276c6fb9ce6c3eb3fd0d` with verified archive SHA256 and its npm workspace lockfile. The native postinstall generates the bundled example index; the original demo HTML loads the built UI. This is a source snapshot, not a newly tagged GitHub release.

The final image serves only static assets through pinned nginx as UID 101, with a read-only root and temporary nginx files. Port `127.0.0.1:11083` is configurable with `JSCAD_PORT`; the internal address is `http://jscad:8080`. No PHP/Perl remote-file proxy, host filesystem mount or CAD command execution service is installed.

## Using it with a project

Have Portal create a JSCAD JavaScript source file in the intended Playground project using the documented `@jscad/modeling` API. Load that file through the editor's file controls, adjust its parameters, and export the resulting geometry. Keep the source alongside the exported model for reproducibility. Example designs are bundled locally.

The browser evaluates design code and renders the geometry. Import only code you intend to execute; a remote design is not inert model data. The upstream URL loading feature remains subject to browser CORS because no server-side fetch proxy is deployed.

There is no server document database or persistent Docker volume. Browser settings are origin-specific; save source and model downloads explicitly. Installing this extension does not automatically place exports in Playground or add an npm dependency to a project. Printing requires an appropriate slicer/printer workflow outside this recipe.

## Resources and verification

No server GPU or inference model is required. Rendering still depends on browser WebGL support, graphics drivers and client memory, and complex Boolean operations can be expensive. The static server is limited to 256 MiB and half a CPU; those limits do not bound browser calculations.

The nginx runtime supports Linux amd64/arm64 under Docker on Windows, Linux and macOS. Source/dependency build, browser controls, design execution and export remain runtime-pending. Definition and staging checks do not prove those behaviors.

Upstream: https://github.com/jscad/OpenJSCAD.org/tree/f245ea3a5072024b789f276c6fb9ce6c3eb3fd0d/packages/web

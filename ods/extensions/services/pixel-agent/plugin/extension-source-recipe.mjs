// Compile ODS packaging conventions, never invent application behavior.
// The model supplies the researched Dockerfile, entrypoint and health probe;
// the normal API validator and source/provenance checks remain authoritative.
export const sourceRecipeSchema = {
  type: 'object', additionalProperties: false,
  required: ['repository', 'commit', 'serviceId', 'name', 'port'],
  // Cross-field validation lives in compileSourceRecipe, which returns the
  // specific missing requirement. Native if/then validation otherwise stops
  // before that diagnostic and reports only "must match then schema".
  properties: {
    repository: {type: 'string'}, commit: {type: 'string', pattern: '^[a-f0-9]{40}$'},
    serviceId: {type: 'string', pattern: '^[a-z0-9][a-z0-9-]{0,63}$'},
    name: {type: 'string'},
    description: {type:'string',maxLength:600,description:'Optional factual project purpose supported by the inspected repository. Empty means omitted. Do not claim installation, compatibility or verification status.'},
    dockerfile: {type: 'string', description: 'Observed repository-relative Dockerfile path. Use this OR dockerfileInline.'},
    dockerfileInline: {type: 'string', description: 'Complete project-specific Dockerfile if upstream has none. COPY the checked-out source and install that source, not a same-named registry package. Research dependencies and the actual entrypoint first. Shell dollars are escaped by ODS.'},
    pythonVersion: {type: 'string', pattern: '^3\\.(10|11|12|13|14)$', description: 'Alternative to dockerfile/dockerfileInline ONLY for an inspected installable Python project (pyproject.toml or setup.py). Choose a version supported by its metadata. ODS retains the pinned Git metadata, fetches its tag history for SCM versioning such as hatch-vcs, pip-installs the whole checkout and runs pip check. Supply the real application command, or pythonImports for a library. Other OS packages or custom build steps still require a researched Dockerfile.'},
    pythonImports: {type: 'array', minItems: 1, maxItems: 16, items: {type: 'string', pattern: '^[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)*$'}, description: 'For a Python LIBRARY only: actual import module names observed in its source or documented usage, e.g. ["actual_package"]. Requires pythonVersion and cliOnly=true. Use instead of command; ODS imports these modules in the built image and checks the exit status. Do not assume the distribution name is also a CLI executable or an import name.'},
    pythonVerification: {type:'object', additionalProperties:false, required:['expression','expected'], properties:{
      expression:{type:'string',minLength:1,maxLength:2048,description:'Optional documented Python expression evaluated after pythonImports inside the installed image. Refer to imported modules as modules["module.name"], for example modules["humanize"].intcomma(12345). Statements, shell commands and workspace paths do not belong here.'},
      expected:{type:'string',maxLength:2048,description:'Exact expected str(expression) from the documented function. A mismatch fails the managed installation verification.'},
    },description:'Optional functional check for a Python library. Requires pythonImports; the managed one-shot verification still imports every module first. Use only an observed documented behavior.'},
    port: {type: 'integer', minimum: 0, maximum: 65535, description: 'Actual HTTP application port, or 0 for a CLI-only image.'},
    healthPath: {type: 'string', description: 'Required for a web service: actual HTTP health path. Omit for a CLI-only image.'},
    healthcheck: {type: 'array', minItems: 2, items: {type: 'string'}, description: 'Required for a web service: real Docker healthcheck starting with CMD or CMD-SHELL. For cliOnly, omit; ODS verifies the command exit instead.'},
    command: {type: 'array', minItems: 1, items: {type: 'string', minLength: 1}, description: 'For cliOnly=true, supply this OR pythonVersion plus pythonImports: the real verification executable and arguments, for example an upstream self-test. Dockerfile CMD does not replace this field. For web services only, omit to retain the Dockerfile CMD.'},
    cliOnly: {type: 'boolean', description: 'Only for an upstream CLI/library without a server: command must run a real successful application verification and exit. No web endpoint will be advertised.'},
  },
};

export function compileSourceRecipe(source) {
  if (!source || typeof source !== 'object' || Array.isArray(source)
      || Object.keys(source).some(key => !Object.hasOwn(sourceRecipeSchema.properties, key))) throw Error('Use only the documented source fields.');
  const missing = sourceRecipeSchema.required.filter(key => !Object.hasOwn(source, key));
  if (missing.length) throw Error('Missing source fields: ' + missing.join(', ') + '. Read this tool schema. A CLI-only image needs cliOnly=true, port=0 and a real verification command.');
  const {repository, commit, serviceId, name, port, healthPath = '', healthcheck, cliOnly = false} = source;
  let command = source.command;
  const verification = source.pythonVerification;
  if (verification !== undefined && (source.pythonImports === undefined
      || !verification || typeof verification !== 'object' || Array.isArray(verification)
      || Object.keys(verification).sort().join() !== 'expected,expression'
      || typeof verification.expression !== 'string' || !verification.expression.trim()
      || verification.expression.length > 2048 || verification.expression.includes('\0')
      || typeof verification.expected !== 'string' || verification.expected.length > 2048
      || verification.expected.includes('\0'))) {
    throw Error('pythonVerification requires pythonImports, a documented expression up to 2048 characters and its exact expected string output.');
  }
  if (source.pythonImports !== undefined) {
    const modules = source.pythonImports;
    if (cliOnly !== true || source.pythonVersion === undefined || command !== undefined
        || !Array.isArray(modules) || modules.length < 1 || modules.length > 16
        || modules.some(name => typeof name !== 'string' || name.length > 128
          || !/^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/.test(name))) {
      throw Error('pythonImports requires pythonVersion, cliOnly=true and observed Python module names. Supply pythonImports OR command, not both.');
    }
    command = ['python', '-c', verification === undefined
      ? `import importlib; [importlib.import_module(name) for name in ${JSON.stringify(modules)}]`
      : [
        'import importlib',
        `modules = {name: importlib.import_module(name) for name in ${JSON.stringify(modules)}}`,
        `actual = eval(${JSON.stringify(verification.expression)}, {"__builtins__": {"str": str, "repr": repr, "len": len, "int": int, "float": float, "bool": bool}}, {"modules": modules})`,
        `assert str(actual) == ${JSON.stringify(verification.expected)}, "Documented Python function returned an unexpected value"`,
      ].join('\n')];
  }
  const issues = [];
  if (source.description !== undefined && (typeof source.description !== 'string' || source.description.length > 600))
    issues.push('description: expected an optional project summary up to 600 characters');
  if (typeof repository !== 'string' || !/^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\/?$/.test(repository))
    issues.push('repository: expected a public https://github.com/OWNER/REPO URL');
  if (typeof commit !== 'string' || !/^[a-f0-9]{40}$/.test(commit))
    issues.push('commit: expected a verified full 40-character lowercase commit SHA');
  if (typeof serviceId !== 'string' || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(serviceId))
    issues.push('serviceId: expected a project identifier of 1-64 lowercase letters, digits or hyphens, starting with a letter or digit; not a tool name');
  if (typeof name !== 'string' || !name.trim() || name.length > 160)
    issues.push('name: expected a nonempty display name up to 160 characters');
  if (!Number.isInteger(port) || port < 0 || port > 65535)
    issues.push('port: expected an integer from 0 to 65535');
  if (typeof healthPath !== 'string' || healthPath.length > 256 || (healthPath !== '' && !/^\/[A-Za-z0-9_/.~-]*$/.test(healthPath)))
    issues.push('healthPath: expected an empty string or an HTTP path starting with /');
  if (typeof cliOnly !== 'boolean') issues.push('cliOnly: expected a boolean');
  if (source.pythonVersion !== undefined && (typeof source.pythonVersion !== 'string' || !/^3\.(10|11|12|13|14)$/.test(source.pythonVersion)))
    issues.push('pythonVersion: expected a JSON string such as "3.10", not a number; select a supported version from inspected project metadata');
  if (issues.length) throw Error(issues.join('; ') + '. Correct these fields on the same tool.');
  const strings = value => Array.isArray(value) && value.length > 0 && value.length <= 32
    && value.every(item => typeof item === 'string' && item.length > 0 && item.length <= 4096 && !item.includes('\0'));
  if (cliOnly && command === undefined) {
    throw Error('cliOnly=true requires source.command with the real verification executable/arguments, or pythonVersion plus pythonImports for an inspected Python library. Dockerfile CMD alone does not supply this field. Keep the remaining source fields and correct this omission.');
  }
  if ((!cliOnly || healthcheck !== undefined) && (!strings(healthcheck) || healthcheck.length < 2 || !['CMD', 'CMD-SHELL'].includes(healthcheck[0]))) {
    throw Error(cliOnly
      ? 'Remove source.healthcheck for cliOnly=true. ODS verifies the exit of source.command or imports pythonImports; a CLI-only extension has no web health endpoint.'
      : 'source.healthcheck must be an argument array starting with CMD or CMD-SHELL, not a Compose object. A web service needs its actual health probe.');
  }
  if (command !== undefined && !strings(command)) throw Error('Supply the actual command as a nonempty argument array.');
  if (cliOnly ? (port !== 0 || healthPath !== '' || !command) : (port === 0 || !healthPath)) {
    throw Error('A web service needs its real port and health path. A CLI-only image needs port 0, empty healthPath and a verification command.');
  }
  const hasFile = typeof source.dockerfile === 'string' && source.dockerfile.length > 0;
  const hasInline = typeof source.dockerfileInline === 'string' && source.dockerfileInline.trim().length > 0;
  const hasPython = source.pythonVersion !== undefined;
  if (hasPython && !command) throw Error('source.command is required with pythonVersion: supply the actual application entrypoint or verification command. ODS does not invent an entrypoint.');
  if (hasPython && (typeof source.pythonVersion !== 'string' || !/^3\.(10|11|12|13|14)$/.test(source.pythonVersion))) {
    throw Error('pythonVersion must be a supported Python 3 minor version selected from the inspected project metadata.');
  }
  if (Number(hasFile) + Number(hasInline) + Number(hasPython) !== 1 || (hasInline && source.dockerfileInline.length > 20000)) {
    throw Error('Supply exactly one inspected dockerfile path, complete dockerfileInline, or pythonVersion for a standard installable Python source project.');
  }
  if (hasInline && !/^\s*COPY\s+(?!--from=)/im.test(source.dockerfileInline)) {
    throw Error('The Dockerfile must COPY and use the checked-out repository source. Installing a same-named registry package does not install this verified commit.');
  }
  const canonicalRepository = repository.replace(/\/$/, '').replace(/\.git$/, '');
  const escapeCompose = value => value.replace(/\$/g, '$$$$');
  // This installs the selected checkout using its own packaging metadata. The
  // host passes BUILDKIT_CONTEXT_KEEP_GIT_DIR=1 for the pinned Git context;
  // git and its tag history must also exist inside the image for SCM version
  // builders. BuildKit supplies only a shallow, untagged checkout. It
  // neither substitutes a registry package nor invents application behavior.
  // Dependency/build/import failures remain real installer failures.
  const inline = hasPython ? [
    `FROM python:${source.pythonVersion}-slim`,
    'RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*',
    'WORKDIR /opt/ods/source',
    'COPY . .',
    `RUN if [ "$(git rev-parse --is-shallow-repository)" = true ]; then git fetch --quiet --unshallow --tags origin; else git fetch --quiet --tags origin; fi && git rev-parse HEAD | grep -Fx '${commit}'`,
    'RUN python -m pip install --no-cache-dir . && python -m pip check',
    // Do not let the checkout shadow the installed package during verification.
    'WORKDIR /opt/ods',
    '',
  ].join('\n') : source.dockerfileInline;
  const portVariable = serviceId.replace(/-/g, '_').toUpperCase() + '_PORT';
  const service = {
    container_name: `ods-${serviceId}`,
    image: `ods-source-${serviceId}:${commit}`,
    build: {context: `${canonicalRepository}.git#${commit}`,
      ...(hasFile ? {dockerfile: source.dockerfile} : {dockerfile_inline: escapeCompose(inline)})},
    pull_policy: 'never',
    ...(!cliOnly ? {healthcheck: {test: healthcheck.map(escapeCompose), interval: '30s', timeout: '10s', retries: 3}} : {}),
    ...(command ? {command: command.map(escapeCompose)} : {}),
    ...(!cliOnly ? {ports: [`127.0.0.1:\${${portVariable}:-${port}}:${port}`], restart: 'unless-stopped'} : {}),
  };
  return {repository: canonicalRepository, commit,
    manifest: {schema_version: 'ods.services.v1', service: {
      id: serviceId, name, ...(source.description?.trim() ? {description:source.description.trim()} : {}), type: 'docker', category: 'optional', compose_file: 'compose.yaml',
      port, health: healthPath, ...(cliOnly ? {startup_check: false, external_link: false}
        : {external_port_env: portVariable, external_port_default: port}),
    }}, compose: {services: {[serviceId]: service}},
  };
}

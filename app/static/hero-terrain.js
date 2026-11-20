(() => {
  const material = document.querySelector('.hero-material');
  const canvas = material.querySelector('canvas');
  const landing = material.closest('#discovery');
  const motion = matchMedia('(prefers-reduced-motion: no-preference)');
  const mobile = matchMedia('(max-width: 820px), (pointer: coarse)');
  const root = document.documentElement;
  const saveData = navigator.connection;

  const vertexSource = `
    attribute vec2 aGrid;
    uniform float uPhase;
    uniform float uAspect;
    uniform float uMobile;
    varying mediump vec3 vNormal;
    varying mediump vec3 vPosition;
    varying mediump float vDepth;

    float shape(vec2 p) {
      float x = p.x;
      float z = p.y;
      float flank = 1.0 - exp(-x*x * .053);
      float nearOffset = (3.15 + uMobile * 3.4) - 1.12 * sin(x * .32 + .43)
        + (.85 + uMobile * .52) * sin(uPhase + x * .32);
      float farOffset = 11.1 + 2.5 * cos(.20 * x + .76)
        + .61 * sin(uPhase + 1.0 + x * .19);
      float near = (z - nearOffset) / 3.05;
      float far = (z - farOffset) / 4.5;
      vec2 hollow = vec2((z - 8.6) / 4.9, (x + 1.25) / 4.4);
      float basin = exp(-dot(hollow, hollow));
      float lift = .55 * smoothstep(-1.8, 8.0, x) + uMobile * .24 * sin(x * .46 - uPhase);
      return .23 + (1.43 + 1.06 * flank + lift) * exp(-near * near)
        + (1.56 + .83 * flank + .17 * cos(x * .5)) * exp(-far * far) - .73 * basin
        + .16 * sin(x * .46 + z * .37 - uPhase) * exp(-.045 * z);
    }

    void main() {
      float x = aGrid.x * 34.0;
      float z = 1.25 + pow(aGrid.y, 1.35) * 36.0;
      vec2 p = vec2(x, z);
      float y = shape(p);
      float h = .11;
      float dx = (shape(p + vec2(h, 0.0)) - shape(p - vec2(h, 0.0))) / (2.0 * h);
      float dz = (shape(p + vec2(0.0, h)) - shape(p - vec2(0.0, h))) / (2.0 * h);
      vNormal = normalize(vec3(-dx, 1.0, -dz));
      vPosition = vec3(x, y, z);
      vDepth = z;
      float sway = sin(uPhase - .37);
      float cameraX = sway * mix(.57, .31, uMobile);
      float cameraY = 3.05 + .13 * cos(uPhase) + uMobile * .32;
      float focal = mix(1.47, 1.17, uMobile);
      float offsetY = .08 + .039 * sin(uPhase + .4) - uMobile * .023;
      float horizontal = (x - cameraX) * focal / uAspect + z * .045 * sway;
      float vertical = (y - cameraY) * focal + z * offsetY;
      gl_Position = vec4(horizontal, vertical, z - 1.3, z);
    }
  `;

  const fragmentSource = `
    #ifdef GL_FRAGMENT_PRECISION_HIGH
      precision highp float;
    #else
      precision mediump float;
    #endif
    uniform float uPhase;
    uniform float uMobile;
    uniform vec3 uShadow;
    uniform vec3 uFace;
    uniform vec3 uLight;
    uniform vec3 uMist;
    uniform float uHeight;
    varying mediump vec3 vNormal;
    varying mediump vec3 vPosition;
    varying mediump float vDepth;

    void main() {
      vec3 n = normalize(vNormal);
      vec3 lamp = normalize(vec3(.47 + .28 * cos(uPhase), .57, -.69 + .16 * sin(uPhase)));
      float diffuse = max(dot(n, lamp), 0.0);
      float grazing = smoothstep(.40, .90, diffuse);
      float spot = (vPosition.x - 2.3 * sin(uPhase + .5)) / 5.0;
      float sweep = .62 + .38 * exp(-spot * spot);
      float crest = smoothstep(.6, 2.0, vPosition.y);
      vec3 pigment = mix(uShadow, uFace, .19 + .55 * diffuse + .22 * crest);
      pigment = mix(pigment, uLight, grazing * sweep * (.29 + .37 * crest));
      pigment = mix(pigment, uMist, smoothstep(7.0, 21.0, vDepth) * .92);
      vec2 pixel = mod(gl_FragCoord.xy, 71.0);
      float grain = fract(sin(dot(pixel, vec2(15.31, 43.13))) * 951.135);
      pigment += (grain - .5) * mix(.024, .016, uMobile);
      float height = 1.0 - gl_FragCoord.y / uHeight;
      float horizon = (1.0 - smoothstep(8.2, 21.0, vDepth)) * smoothstep(.43, .64, height);
      gl_FragColor = vec4(pigment, horizon);
    }
  `;

  let gl;
  let scene;
  let inView = false;
  let unavailable = false;
  let frame = 0;
  let previous = 0;
  let elapsed = 3;
  let frameCount = 0;
  let slowFrames = 0;
  let quality = 1;
  let needsSize = true;
  let needsPalette = true;
  let gotFirstFrame = false;

  function shader(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
    gl.deleteShader(shader);
    return null;
  }

  function createScene() {
    if (!gl) return null;
    const vertex = shader(gl.VERTEX_SHADER, vertexSource);
    const fragment = shader(gl.FRAGMENT_SHADER, fragmentSource);
    const program = gl.createProgram();
    if (vertex) gl.attachShader(program, vertex);
    if (fragment) gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (vertex) gl.deleteShader(vertex);
    if (fragment) gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      gl.deleteProgram(program);
      return null;
    }

    const columns = 108;
    const rows = 72;
    const vertices = [];
    const indices = [];
    for (let y = 0; y <= rows; y++) {
      for (let x = 0; x <= columns; x++) {
        vertices.push(2 * x / columns - 1, y / rows);
        if (x === columns || y === rows) continue;
        const a = y * (columns + 1) + x;
        const b = a + columns + 1;
        indices.push(a, b, a + 1, a + 1, b, b + 1);
      }
    }
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.STATIC_DRAW);
    const indexBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(indices), gl.STATIC_DRAW);
    const grid = gl.getAttribLocation(program, 'aGrid');
    gl.enableVertexAttribArray(grid);
    gl.vertexAttribPointer(grid, 2, gl.FLOAT, false, 0, 0);
    gl.useProgram(program);
    gl.enable(gl.DEPTH_TEST);
    gl.clearColor(0, 0, 0, 0);

    const uniforms = {};
    for (const name of ['Phase', 'Aspect', 'Mobile', 'Shadow', 'Face', 'Light', 'Mist', 'Height']) {
      uniforms[name] = gl.getUniformLocation(program, 'u' + name);
    }
    return { uniforms, count: indices.length };
  }

  function syncPalette() {
    const style = getComputedStyle(material);
    for (const name of ['Shadow', 'Face', 'Light', 'Mist']) {
      const hex = style.getPropertyValue('--terrain-' + name.toLowerCase()).trim().slice(1);
      const color = Number.parseInt(hex, 16);
      gl.uniform3f(scene.uniforms[name], ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255);
    }
    needsPalette = false;
  }

  function syncSize() {
    const { width, height } = material.getBoundingClientRect();
    if (!width || !height) return;
    const maxPixels = mobile.matches ? 700000 : 2200000;
    const ratio = Math.min(devicePixelRatio, mobile.matches ? 1.5 : 2, Math.sqrt(maxPixels / (width * height))) * quality;
    canvas.width = Math.max(1, Math.floor(width * ratio));
    canvas.height = Math.max(1, Math.floor(height * ratio));
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform1f(scene.uniforms.Aspect, width / height);
    gl.uniform1f(scene.uniforms.Height, canvas.height);
    gl.uniform1f(scene.uniforms.Mobile, Number(mobile.matches));
    needsSize = false;
  }

  function visible() {
    return motion.matches && !saveData?.saveData && !document.hidden &&
      inView && landing.classList.contains('active') && !landing.classList.contains('workspace-open');
  }

  function tick(now) {
    frame = 0;
    if (unavailable || !visible()) return;
    const interval = mobile.matches ? 1000 / 30 : 1000 / 60;
    const delta = previous ? now - previous : interval;
    if (delta >= interval - 1.1) {
      if (previous && delta > interval * 1.55) slowFrames++;
      if (++frameCount >= 45) {
        if (slowFrames >= 10 && quality > .65) {
          quality = Math.max(.65, quality * .82);
          needsSize = true;
        }
        frameCount = 0;
        slowFrames = 0;
      }
      if (needsSize) syncSize();
      if (needsPalette) syncPalette();
      elapsed += Math.min(delta, 70) / 1000;
      gl.uniform1f(scene.uniforms.Phase, elapsed % 16 * Math.PI / 8);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.drawElements(gl.TRIANGLES, scene.count, gl.UNSIGNED_SHORT, 0);
      if (!gotFirstFrame) {
        gotFirstFrame = true;
        material.classList.add('has-terrain');
      }
      previous = now;
    }
    frame = requestAnimationFrame(tick);
  }

  function sync() {
    const active = visible();
    material.classList.toggle('terrain-in-view', active);
    if (!motion.matches || saveData?.saveData) material.classList.remove('has-terrain');
    if (!active || unavailable) {
      cancelAnimationFrame(frame);
      frame = 0;
      previous = 0;
      return;
    }
    if (!scene) {
      gl = canvas.getContext('webgl', { antialias:false, premultipliedAlpha:false, powerPreference:'low-power', failIfMajorPerformanceCaveat:true });
      scene = createScene();
      if (!scene) {
        unavailable = true;
        return;
      }
    }
    if (gotFirstFrame) material.classList.add('has-terrain');
    if (!frame) frame = requestAnimationFrame(tick);
  }

  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault();
    unavailable = true;
    gotFirstFrame = false;
    scene = null;
    material.classList.remove('has-terrain');
    sync();
  });
  canvas.addEventListener('webglcontextrestored', () => {
    unavailable = false;
    needsSize = true;
    needsPalette = true;
    sync();
  });
  const visibleFraction = .42;
  new IntersectionObserver(([entry]) => {
    inView = entry.intersectionRatio >= visibleFraction;
    sync();
  }, { threshold: visibleFraction }).observe(material);
  new ResizeObserver(() => { needsSize = true; }).observe(material);
  new MutationObserver(sync).observe(landing, { attributes:true, attributeFilter:['class'] });
  new MutationObserver(() => { needsPalette = true; }).observe(root, { attributes:true, attributeFilter:['data-theme'] });
  mobile.addEventListener('change', () => { needsSize = true; });
  motion.addEventListener('change', sync);
  saveData?.addEventListener('change', sync);
  document.addEventListener('visibilitychange', sync);
})();

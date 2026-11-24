(() => {
  const material = document.querySelector('.hero-material');
  if (!material) return;

  const titleFont = document.fonts?.load('400 1em "Geist Pixel Circle"')
    .then(fonts => {
      if (fonts.length) document.documentElement.classList.add('hero-font-ready');
    })
    .catch(() => {});

  const canvas = material.querySelector('canvas');
  const gl = canvas.getContext('webgl', { alpha: true, antialias: false, powerPreference: 'low-power' });
  if (!gl) return;

  const period = 9.25;
  const vertex = `
    attribute vec3 samplePoint;
    uniform float time, pixelScale, lightTheme;
    uniform mediump float surfacePass;
    uniform vec2 projection, cameraCenter;
    varying mediump vec2 shade;
    varying mediump float coverage;

    float ease(float low, float high, float value) {
      float t = clamp((value - low) / (high - low), 0.0, 1.0);
      return t * t * t * (t * (6.0 * t - 15.0) + 10.0);
    }

    vec3 ribbon(vec2 uv, float clock, out vec3 tangent) {
      float u = uv.x, v = uv.y;
      float phase = 6.283185 * (.92 * u + .18 - clock / ${period});
      float nearSide = 1.0 - ease(-.09, .51, u);
      float farSide = ease(.42, 1.08, u);
      float axis = .30 + .58 * cos(phase) - .18 * sin(2.0 * phase)
                   + .72 * nearSide - .54 * farSide;
      float breadth = (.73 + .30 * cos(phase - .95)) *
                      (1.06 + .44 * nearSide - .38 * farSide);
      float angle = .52 + 1.34 * sin(phase - .29) + .24 * cos(2.0 * phase) - .70 * nearSide;
      float curl = .55 + 1.60 * pow(.5 + .5 * cos(phase - 1.74), 2.0);
      vec2 crossSection = vec2(sin(curl * v), 1.0 - cos(curl * v)) * breadth / curl;
      float rise = crossSection.x * cos(angle) - crossSection.y * sin(angle);
      float depth = crossSection.x * sin(angle) + crossSection.y * cos(angle);
      float sweep = .52 * sin(phase + .60);
      float crossAngle = curl * v + angle;
      float depthSlope = breadth * sin(crossAngle);
      tangent = vec3(sweep * depthSlope, breadth * cos(crossAngle), depthSlope);
      float x = (u - .50) * 8.65 + sweep * depth;
      return vec3(x, axis + rise, 3.45 + .70 * u + depth - .32 * nearSide - .24 * cos(phase - .65));
    }

    void main() {
      vec2 uv = samplePoint.xy;
      float edge = max(abs(uv.y), abs(2.0*uv.x - 1.0));
      float drift = (1.0 - surfacePass) * smoothstep(.79, 1.42, edge);
      float clock = time - drift * (.19 + .26 * samplePoint.z);
      vec3 dy, unusedTangent;
      vec3 center = ribbon(uv, clock, dy);
      vec2 du = vec2(.0015, 0.0);
      vec3 after = ribbon(uv + du, clock, unusedTangent);
      vec3 before = ribbon(uv - du, clock, unusedTangent);
      vec3 dx = after - before;
      vec3 normal = normalize(cross(dx, dy));
      vec3 view = normalize(-center);
      float facing = clamp(dot(normal, view), -1.0, 1.0);
      float incident = dot(normal, normalize(vec3(-.40, .63, -.65)));
      // Blend the two sides at grazing angles so a turnover cannot flip the lighting in one frame.
      float daylight = mix(max(-incident, 0.0), max(incident, 0.0), smoothstep(-.22, .22, facing));
      float rim = pow(1.0 - abs(facing), 3.4);
      float tone = .04 + .63 * daylight + .46 * rim + .14 * smoothstep(-.33,.33,facing);
      vec2 perspective = center.xy * projection / center.z + cameraCenter;
      // A soft tonal falloff under the text retains the continuous surface.
      vec2 textPosition = (perspective - cameraCenter) / vec2(.55, .28);
      float textLight = exp(-dot(textPosition, textPosition) * 1.4);
      tone = mix(tone, mix(.18, .82, lightTheme), .47 * textLight);
      shade = vec2(tone, samplePoint.z);
      float ends = smoothstep(-.055,.055,uv.x) * (1.0 - smoothstep(.84,1.06,uv.x));
      float grain = (1.0 - smoothstep(.76, 1.34, abs(uv.y))) + .12 * drift;
      float sheet = 1.0 - smoothstep(.73, 1.0, abs(uv.y));
      coverage = ends * mix(grain * mix(.49,.96,samplePoint.z), sheet * mix(.13, .18, lightTheme), surfacePass);
      float depthBias = (1.0 - surfacePass) * (.003 + .012 * drift);
      gl_Position = vec4(perspective * center.z, center.z * 1.06 - 1.0 - depthBias, center.z);
      gl_PointSize = pixelScale * mix(1.56, 2.25, samplePoint.z) * 3.45 / center.z;
      gl_PointSize *= mix(1.0, .70, drift);
    }
  `;

  const fragment = `
    precision mediump float;
    uniform vec3 darkTone, lightTone;
    uniform mediump float surfacePass;
    varying mediump vec2 shade;
    varying mediump float coverage;

    void main() {
      if (coverage < .045) discard;
      float alpha = coverage * smoothstep(.045, .085, coverage);
      if (surfacePass < .5) {
        float radial = length(gl_PointCoord - .5) * 2.0;
        if (radial > 1.0) discard;
        alpha *= 1.0 - smoothstep(.43, 1.0, radial);
      }
      float tone = clamp(shade.x + (shade.y - .5) * .37, .03, .94);
      gl_FragColor = vec4(mix(darkTone, lightTone, tone), alpha);
    }
  `;

  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
  }

  const vs = compile(gl.VERTEX_SHADER, vertex);
  const fs = compile(gl.FRAGMENT_SHADER, fragment);
  if (!vs || !fs) return;
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
  gl.useProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);

  const points = [];
  let seed = 7327;
  function random() {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  }

  // Interleaved levels allow the phone to draw a uniformly distributed subset.
  const tiers = [70000, 90000];
  for (const count of tiers) {
    for (let i = 0; i < count; i++) {
      const loose = random() < .16;
      const u = loose ? -.095 + 1.19*random() : random();
      const v = (2*random() - 1) * (loose ? 1.62 : 1.04);
      points.push(u, v, random());
    }
  }
  function buffer(target, data) {
    const id = gl.createBuffer();
    gl.bindBuffer(target, id);
    gl.bufferData(target, data, gl.STATIC_DRAW);
    return id;
  }

  const positions = buffer(gl.ARRAY_BUFFER, new Float32Array(points));
  const columns = 144;
  const rows = 44;
  const vertices = [];
  const triangles = [];
  for (let u = 0; u <= columns; u++) {
    for (let v = 0; v <= rows; v++) {
      vertices.push(u / columns, 2 * v / rows - 1, .5);
      if (u < columns && v < rows) {
        const i = u * (rows + 1) + v;
        const next = i + rows + 1;
        triangles.push(i, i + 1, next, i + 1, next + 1, next);
      }
    }
  }
  const sheet = buffer(gl.ARRAY_BUFFER, new Float32Array(vertices));
  buffer(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(triangles));
  const position = gl.getAttribLocation(program, 'samplePoint');
  gl.enableVertexAttribArray(position);
  function bindPositions(id) {
    gl.bindBuffer(gl.ARRAY_BUFFER, id);
    gl.vertexAttribPointer(position, 3, gl.FLOAT, false, 0, 0);
  }

  const uniforms = Object.fromEntries(
    ['time', 'pixelScale', 'projection', 'cameraCenter', 'surfacePass', 'lightTheme', 'darkTone', 'lightTone']
      .map(name => [name, gl.getUniformLocation(program, name)]));
  gl.enable(gl.DEPTH_TEST);
  gl.enable(gl.BLEND);
  gl.blendFuncSeparate(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA,gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);

  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let frame = 0;
  let time = 0;
  let previous = 0;
  let visible = true;
  let lost = false;
  let palette;
  let mobile;

  function color(style, name) {
    const hex = style.getPropertyValue(name).trim().slice(1);
    return [0, 2, 4].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255);
  }

  function readTheme() {
    const style = getComputedStyle(root);
    palette = {
      dark: color(style, '--hero-back-face'),
      light: color(style, '--hero-back-rim'),
    };
  }

  const title = document.querySelector('.landing-title');
  const lead = title.querySelector('.title-lead');
  const tail = title.querySelector('.title-tail');

  function fit() {
    const area = material.getBoundingClientRect();
    const width = material.clientWidth;
    const height = material.clientHeight;
    if (!width || !height) {
      material.classList.remove('terrain-rendered');
      return false;
    }
    mobile = width <= 820;
    const scale = Math.min(devicePixelRatio || 1, mobile ? 1.25 : 1.4, 1700 / width);
    const pixelsWide = Math.round(width * scale);
    const pixelsHigh = Math.round(height * scale);
    if (canvas.width !== pixelsWide || canvas.height !== pixelsHigh) {
      canvas.width = pixelsWide;
      canvas.height = pixelsHigh;
    }
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform1f(uniforms.pixelScale, scale);
    // The vertical and horizontal fields of view share the same focal length in CSS pixels.
    const focal = mobile ? Math.max(420, width * .72) : width * .51;
    const seam = (lead.getBoundingClientRect().bottom + tail.getBoundingClientRect().top) / 2;
    gl.uniform2f(uniforms.projection, 2 * focal / width, 2 * focal / height);
    gl.uniform2f(uniforms.cameraCenter, mobile ? -.10 : .0, 1 - 2 * (seam - area.top + (mobile ? 12 : 20)) / height);
    return true;
  }

  function paint() {
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.uniform1f(uniforms.time, reduced.matches ? 0 : time);
    gl.uniform3fv(uniforms.darkTone, palette.dark);
    gl.uniform3fv(uniforms.lightTone, palette.light);
    gl.uniform1f(uniforms.lightTheme, root.dataset.theme === 'light' ? 1 : 0);
    gl.uniform1f(uniforms.surfacePass, 1);
    bindPositions(sheet);
    gl.drawElements(gl.TRIANGLES, triangles.length, gl.UNSIGNED_SHORT, 0);
    gl.uniform1f(uniforms.surfacePass, 0);
    bindPositions(positions);
    gl.drawArrays(gl.POINTS, 0, mobile ? tiers[0] : points.length / 3);
  }

  function step(now) {
    frame = requestAnimationFrame(step);
    const delta = previous ? now - previous : 17;
    if (previous && mobile && delta < 28) return;
    previous = now;
    time = (time + delta / 1000) % period;
    paint();
  }

  function sync() {
    cancelAnimationFrame(frame);
    previous = 0;
    if (lost || !visible || document.hidden || !fit()) return;
    readTheme();
    paint();
    material.classList.add('terrain-rendered');
    if (!reduced.matches) frame = requestAnimationFrame(step);
  }

  reduced.addEventListener('change', sync);
  document.addEventListener('visibilitychange', sync);
  canvas.addEventListener('webglcontextlost', () => {
    lost = true;
    cancelAnimationFrame(frame);
    material.classList.remove('terrain-rendered');
  });
  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ['data-theme'] });
  if ('ResizeObserver' in window) {
    const resize = new ResizeObserver(sync);
    resize.observe(material);
    resize.observe(title);
  } else window.addEventListener('resize', sync);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      sync();
    }).observe(material);
  }
  titleFont?.then(sync);
  sync();
})();

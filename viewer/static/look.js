import * as THREE from 'three';

// materials that recreate the game's toon look from its own textures and parameters

// --- shared look settings ---------------------------------------------------------

export const U = {
  lightDir: { value: new THREE.Vector3(0.3, 0.5, 0.8).normalize() },
  toonStep: { value: 0.4 },
  bright: { value: 1 },
  rim: { value: 1 },
  outlineW: { value: 1 },
  eyeCell: { value: new THREE.Vector2(0, 0) },
  eyeHi: { value: 1 },
};

const VERT = /* glsl */`
#include <common>
#include <skinning_pars_vertex>
varying vec2 vUv;
varying vec2 vUv1;
varying vec3 vNormal;
varying vec3 vViewPos;
void main() {
  #include <skinbase_vertex>
  #include <beginnormal_vertex>
  #include <skinnormal_vertex>
  #include <begin_vertex>
  #include <skinning_vertex>
  vec4 mv = modelViewMatrix * vec4(transformed, 1.0);
  vNormal = normalize(normalMatrix * objectNormal);
  vViewPos = -mv.xyz;
  vUv = uv;
  #ifdef USE_UV1
    vUv1 = uv1;
  #else
    vUv1 = uv;
  #endif
  gl_Position = projectionMatrix * mv;
}`;

// toon shading in the game's terms: _MainTex lit color, _ToonMap shadow color, and
// _TripleMaskMap R (painted shadow tendency, 0 = always shaded) / G (specular mask).
// background Umas: an area mask recolours grey textures. per channel, 0.5 keeps the texture;
// towards 0 it tints with that channel's first colour, towards 1 with its second (R1 R2 G1 G2 B1
// B2). the shadow side uses its own six.
const MASK_GLSL = /* glsl */`
uniform sampler2D uMask;
uniform float uHasMask;
uniform float uHairCut;
uniform vec3 uMaskC[6];
uniform vec3 uMaskT[6];
float maskW(float v, float dir) { return clamp(((v - 0.5) * dir * 2.0 - 0.02) / 0.96, 0.0, 1.0); }
vec3 maskTint(vec3 m, bool toon) {
  vec3 t = vec3(1.0);
  for (int i = 0; i < 3; i++) {
    float v = i == 0 ? m.r : i == 1 ? m.g : m.b;
    t = mix(t, toon ? uMaskT[i * 2] : uMaskC[i * 2], maskW(v, -1.0));
    t = mix(t, toon ? uMaskT[i * 2 + 1] : uMaskC[i * 2 + 1], maskW(v, 1.0));
  }
  return t;
}`;

const TOON_FRAG = /* glsl */`
uniform sampler2D uMain;
uniform sampler2D uToon;
uniform sampler2D uTriple;
uniform float uHasToon;
uniform float uHasTriple;
uniform vec3 lightDir;
uniform float toonStep;
uniform float uFeather;
uniform float bright;
uniform float rim;
uniform float uFace;
uniform vec4 uRimColor;
uniform vec3 uSpecColor;
uniform float uCutout;
varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vViewPos;
${MASK_GLSL}
void main() {
  vec4 diff = texture2D(uMain, vUv);
  if (diff.a < uCutout) discard;
  vec3 shade = uHasToon > 0.5 ? texture2D(uToon, vUv).rgb : diff.rgb * vec3(0.78, 0.76, 0.88);
  vec4 tri = uHasTriple > 0.5 ? texture2D(uTriple, vUv) : vec4(1.0, 0.0, 1.0, 1.0);
  if (uHasMask > 0.5) {
    if (uHairCut > 0.0 && tri.b < uHairCut) discard;
    vec3 m = texture2D(uMask, vUv).rgb;
    diff.rgb *= maskTint(m, false);
    shade *= maskTint(m, true);
  }
  vec3 N = normalize(vNormal);
  if (!gl_FrontFacing) N = -N;
  vec3 V = normalize(vViewPos);
  float ndl = dot(N, lightDir) * 0.5 + 0.5;
  float bias = uFace > 0.5 ? 1.0 : tri.r * 1.25;
  float t = ndl * bias;
  float lit = smoothstep(toonStep - uFeather, toonStep + uFeather, t);
  if (uFace > 0.5) lit = mix(0.55, 1.0, lit);
  vec3 col = mix(shade, diff.rgb, lit);
  vec3 H = normalize(lightDir + V);
  float spec = (uFace > 0.5 ? 0.0 : tri.g) * smoothstep(0.86, 0.9, max(dot(N, H), 0.0)) * lit;
  col += uSpecColor * spec * 0.5;
  float r = 1.0 - max(dot(N, V), 0.0);
  float rimMask = smoothstep(0.6, 0.8, r) * (0.3 + 0.7 * lit);
  col += uRimColor.rgb * uRimColor.a * rimMask * rim * 0.9;
  gl_FragColor = vec4(col * bright, 1.0);
}`;

const UNLIT_FRAG = /* glsl */`
uniform sampler2D uMain;
uniform float bright;
uniform float uCutout;
varying vec2 vUv;
${MASK_GLSL}
void main() {
  vec4 c = texture2D(uMain, vUv);
  if (c.a < uCutout) discard;
  if (uHasMask > 0.5) c.rgb *= maskTint(texture2D(uMask, vUv).rgb, false);
  gl_FragColor = vec4(c.rgb * bright, 1.0);
}`;

// irises live in a 4x2 atlas: each eye's UV0 already picks its row (one eye per row) and spans
// one cell horizontally, so only the column (iris variant) is chosen here. highlights ride on UV1.
const EYE_FRAG = /* glsl */`
uniform sampler2D uMain;
uniform sampler2D uHi0;
uniform sampler2D uHi1;
uniform vec2 eyeCell;
uniform float eyeHi;
uniform float bright;
varying vec2 vUv;
varying vec2 vUv1;
${MASK_GLSL}
void main() {
  vec2 cuv = vec2((eyeCell.x + vUv.x) * 0.25, vUv.y);
  vec3 c = texture2D(uMain, cuv).rgb;
  if (uHasMask > 0.5) c *= maskTint(texture2D(uMask, cuv).rgb, false);
  c += (texture2D(uHi0, vUv1).rgb + texture2D(uHi1, vUv1).rgb) * eyeHi;
  gl_FragColor = vec4(min(c, vec3(1.0)) * bright, 1.0);
}`;

// chibi face parts: tiles in an atlas. uTile.xy shifts the part, or for the eyes the first half
// of the cell (one eye), uTile.zw the other half (uSplit is where the halves meet).
const TILE_FRAG = /* glsl */`
uniform sampler2D uMain;
uniform vec4 uTile;
uniform float uSplit;
uniform float bright;
uniform float uCutout;
varying vec2 vUv;
void main() {
  vec2 o = vUv.x < uSplit ? uTile.xy : uTile.zw;
  vec4 c = texture2D(uMain, vUv + o);
  if (c.a < uCutout) discard;
  gl_FragColor = vec4(c.rgb * bright, 1.0);
}`;

// blush: multiplies what is already drawn, leaves alpha alone
const CHEEK_FRAG = /* glsl */`
uniform sampler2D uMain;
uniform float uRate;
varying vec2 vUv;
void main() {
  vec4 c = texture2D(uMain, vUv);
  gl_FragColor = vec4(mix(vec3(1.0), c.rgb, c.a * uRate), 1.0);
}`;

const OUTLINE_VERT = /* glsl */`
#include <common>
#include <skinning_pars_vertex>
uniform float outlineW;
uniform float uWidth;
varying vec2 vUv;
void main() {
  #include <skinbase_vertex>
  #include <beginnormal_vertex>
  #include <skinnormal_vertex>
  #include <begin_vertex>
  #include <skinning_vertex>
  #if defined(USE_COLOR_ALPHA) || defined(USE_COLOR)
    float thick = color.r;
  #else
    float thick = 1.0;
  #endif
  vec4 mv = modelViewMatrix * vec4(transformed, 1.0);
  vec3 n = normalize(normalMatrix * objectNormal);
  // width in view space, grown with distance so lines keep a steady weight on screen
  float w = outlineW * uWidth * thick * 0.0016 * clamp(-mv.z, 0.6, 8.0);
  mv.xyz += n * w;
  // pushed back a little so the hull only shows at silhouettes. without this, inner creases
  // (lips, mouth corners) draw stray strokes across the face.
  mv.z -= 0.008;
  vUv = uv;
  gl_Position = projectionMatrix * mv;
}`;

const OUTLINE_FRAG = /* glsl */`
uniform sampler2D uMain;
uniform sampler2D uTriple;
uniform vec3 uLine;
varying vec2 vUv;
${MASK_GLSL}
void main() {
  vec3 c = texture2D(uMain, vUv).rgb;
  if (uHasMask > 0.5) {
    if (uHairCut > 0.0 && texture2D(uTriple, vUv).b < uHairCut) discard;
    c *= maskTint(texture2D(uMask, vUv).rgb, true);
  }
  gl_FragColor = vec4(c * uLine, 1.0);
}`;

const WHITE = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
WHITE.needsUpdate = true;
const BLACK = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
BLACK.needsUpdate = true;

export function kindOf(ud) {
  const s = ud.shader || '', n = (ud.mesh || '');
  if (s.includes('MiniCharaEye')) return 'minieye';
  if (s.includes('UnlitZIgnoreTransparent')) return 'minimayu';
  if (n === 'M_Mouth') return 'minimouth';
  if (s.includes('Cheek') || s.includes('UnlitMultiply')) return 'cheek';
  if (s.includes('Tear')) return 'tear';
  if (s.includes('ToonEye')) return 'eye';
  if (s.includes('ToonMayu')) return 'mayu';
  if (s.includes('ToonFace')) return 'face';
  if (s.includes('ToonHair')) return 'hair';
  if (n === 'M_Tail' || s.includes('NolineToon')) return 'tail';
  return 'body';
}

function rgba(c, fallback) { return c ? new THREE.Vector4(c[0], c[1], c[2], c[3]) : fallback; }

/** opts (props): cutout = alpha below which pixels are dropped, side. Kind 'flat' draws
 *  the texture as is, without shading. */
const hexVec = h => new THREE.Vector3(parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255);

/** The recolour uniforms; inert for everything that has no mask. */
function maskUniforms(ud, tex) {
  const m = ud.mask, on = !!(m && tex._MaskColorTex);
  return {
    uMask: { value: on ? tex._MaskColorTex : WHITE }, uHasMask: { value: on ? 1 : 0 }, uHairCut: { value: on ? m.cutoff || 0 : 0 },
    uMaskC: { value: on ? m.colors.map(hexVec) : Array.from({ length: 6 }, () => new THREE.Vector3(1, 1, 1)) },
    uMaskT: { value: on ? m.toon.map(hexVec) : Array.from({ length: 6 }, () => new THREE.Vector3(1, 1, 1)) },
  };
}

export function makeMaterial(kind, ud, tex, opts = {}) {
  const f = ud.floats || {}, c = ud.colors || {};
  const main = tex._MainTex || WHITE;
  const mask = maskUniforms(ud, tex);
  const cutout = { value: opts.cutout ?? 0 };
  if (kind === 'cheek') {
    return new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: CHEEK_FRAG,
      uniforms: { uMain: { value: main }, uRate: { value: f._CheekRate ?? 1 } },
      transparent: true, depthWrite: false,
      blending: THREE.CustomBlending, blendEquation: THREE.AddEquation,
      blendSrc: THREE.DstColorFactor, blendDst: THREE.ZeroFactor,
      blendSrcAlpha: THREE.ZeroFactor, blendDstAlpha: THREE.OneFactor,
    });
  }
  if (kind === 'minieye' || kind === 'minimayu' || kind === 'minimouth') {
    return new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: TILE_FRAG,
      uniforms: { uMain: { value: main }, uTile: { value: new THREE.Vector4() }, uSplit: { value: kind === 'minieye' ? 0.125 : 2 },
                  bright: U.bright, uCutout: { value: kind === 'minimayu' ? 0.5 : 0 } },
    });
  }
  if (kind === 'eye') {
    return new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: EYE_FRAG,
      uniforms: { uMain: { value: main }, uHi0: { value: tex._High0Tex || BLACK }, uHi1: { value: tex._High1Tex || BLACK },
                  eyeCell: U.eyeCell, eyeHi: U.eyeHi, bright: U.bright, ...mask },
    });
  }
  if (kind === 'mayu' || kind === 'flat') {
    return new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: UNLIT_FRAG,
      uniforms: { uMain: { value: main }, bright: U.bright, uCutout: kind === 'mayu' ? { value: 0.5 } : cutout, ...mask },
      side: opts.side ?? THREE.FrontSide,
    });
  }
  return new THREE.ShaderMaterial({
    vertexShader: VERT, fragmentShader: TOON_FRAG,
    uniforms: {
      uMain: { value: main }, uToon: { value: tex._ToonMap || WHITE }, uTriple: { value: tex._TripleMaskMap || WHITE },
      uHasToon: { value: tex._ToonMap ? 1 : 0 }, uHasTriple: { value: tex._TripleMaskMap ? 1 : 0 },
      lightDir: U.lightDir, toonStep: U.toonStep, bright: U.bright, rim: U.rim,
      uFeather: { value: Math.max(f._ToonFeather ?? 0.02, 0.02) },
      uFace: { value: kind === 'face' ? 1 : 0 },
      uRimColor: { value: rgba(c._RimColor, new THREE.Vector4(1, 1, 1, 0.3)) },
      uSpecColor: { value: new THREE.Vector3(...(c._SpecularColor || [1, 1, 1]).slice(0, 3)) },
      uCutout: cutout, ...mask,
    },
    side: opts.side ?? (kind === 'hair' ? THREE.DoubleSide : THREE.FrontSide),
  });
}

export function makeOutline(ud, tex) {
  const f = ud.floats || {};
  return new THREE.ShaderMaterial({
    vertexShader: OUTLINE_VERT, fragmentShader: OUTLINE_FRAG,
    uniforms: {
      uMain: { value: tex._MainTex || WHITE }, uLine: { value: new THREE.Vector3(0.42, 0.36, 0.38) },
      outlineW: U.outlineW, uWidth: { value: (f._OutlineWidth ?? 0.325) / 0.325 },
      uTriple: { value: tex._TripleMaskMap || WHITE }, ...maskUniforms(ud, tex),
    },
    side: THREE.BackSide, vertexColors: true,
  });
}

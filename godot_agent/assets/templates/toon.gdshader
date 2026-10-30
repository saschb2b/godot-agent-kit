// godot-agent toon v1: two-tone toon light for characters built with the kit's chibi helpers.
// Vertex colour = the flat swatch (linear). Vertex alpha = shade class:
//   1.00 cloth, hair, leather, metal   0.75 skin   0.50 face paint   0.25 unlit glint
// Two tones with a crisp terminator. The shadow side is albedo * tint, chosen here rather
// than by the scene's ambient, so a portrait, a menu and every map shade a character alike.
// A light from behind draws only a rim. No specular, no reflections.
// (The kit refreshes this file while this header stays; remove the header to keep your edits.)
shader_type spatial;
render_mode ambient_light_disabled, specular_disabled, shadows_disabled;

uniform vec3 shadow_tint : source_color = vec3(0.64, 0.59, 0.68);      // cool violet shade for cloth
uniform vec3 skin_shadow_tint : source_color = vec3(0.88, 0.68, 0.66); // warm, light shade for skin
uniform float paint_contrast : hint_range(0.0, 1.0) = 0.4;              // how far face paint darkens
uniform float terminator : hint_range(-0.5, 0.5) = 0.0;                 // + moves the shadow toward the light
uniform float softness : hint_range(0.001, 0.1) = 0.02;                 // the terminator's width
uniform float rim_width : hint_range(0.05, 0.7) = 0.45;
uniform float back_rim : hint_range(0.0, 2.0) = 0.8;
uniform float front_rim : hint_range(0.0, 1.0) = 0.12;
// The Compatibility renderer (web builds) draws the same vertex colours darker and more
// saturated than Forward+: set these from code there (on a per-node copy of the material).
// At their defaults they change nothing.
uniform float compat_lift : hint_range(0.1, 3.0) = 1.0;   // multiplies albedo and emission
uniform float compat_gamma : hint_range(0.2, 1.0) = 1.0;  // < 1 lifts the dark swatches more

varying vec3 shade_tint;

vec3 swatch(vec3 c) {
	return pow(c, vec3(compat_gamma)) * compat_lift;
}

void fragment() {
	float shade_class = COLOR.a;
	vec3 tint = shadow_tint;
	if (shade_class < 0.875) {
		tint = shade_class > 0.625 ? skin_shadow_tint : mix(vec3(1.0), shadow_tint, paint_contrast);
	}
	bool unlit = shade_class < 0.375;
	vec3 base = swatch(COLOR.rgb);
	ALBEDO = unlit ? vec3(0.0) : base;
	// the shadow tone, lit by nothing; a glint keeps its full brightness in either renderer
	EMISSION = unlit ? pow(COLOR.rgb, vec3(compat_gamma)) : base * tint;
	shade_tint = tint;
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;
}

void light() {
	float ndl = dot(NORMAL, LIGHT);
	float edge = 1.0 - clamp(dot(NORMAL, VIEW), 0.0, 1.0);
	float rim = smoothstep(1.0 - rim_width - softness, 1.0 - rim_width + softness, edge) * step(0.0, ndl);
	vec3 energy = LIGHT_COLOR / PI;
	if (LIGHT_IS_DIRECTIONAL && dot(LIGHT, VIEW) < -0.2) {
		// A light from behind the character draws only a crisp rim.
		DIFFUSE_LIGHT += energy * rim * back_rim;
	} else {
		float lit = smoothstep(terminator - softness, terminator + softness, ndl) * ATTENUATION;
		DIFFUSE_LIGHT += energy * lit * ((vec3(1.0) - shade_tint) + rim * front_rim);
	}
}

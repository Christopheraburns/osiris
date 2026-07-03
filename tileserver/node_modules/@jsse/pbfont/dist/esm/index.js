import { create, fromBinary, toBinary, toJsonString } from "@bufbuild/protobuf";
import { fontstackSchema, glyphsSchema } from "./gen/glyphs_pb.js";
function debug(buffer) {
    if (buffer instanceof Uint8Array) {
        const g = fromBinary(glyphsSchema, buffer);
        return toJsonString(glyphsSchema, g);
    }
    return toJsonString(glyphsSchema, buffer);
}
export function decode(buffer) {
    return fromBinary(glyphsSchema, buffer);
}
export function encode(data) {
    return toBinary(glyphsSchema, data);
}
function range256(start) {
    if (start < 0 || start > 65_535)
        throw new Error(`start must be between 0 and 255; given ${start}`);
    const start256 = Math.trunc(start / 256) * 256;
    const stop256 = start256 + 255;
    return {
        start: start256,
        stop: stop256,
        str: `${start256}-${stop256}`,
    };
}
function parseRange(range) {
    const [start, stop] = range.split("-").map(Number);
    if (start === undefined ||
        stop === undefined ||
        Number.isNaN(start) ||
        Number.isNaN(stop) ||
        start < 0 ||
        stop < 0 ||
        start > 65_535 ||
        stop > 65_535) {
        throw new Error(`range must be in the form 'start-stop'; given ${range}`);
    }
    return { start, stop, str: `${start}-${stop}` };
}
function combineRanges(ranges) {
    const parsedRanges = ranges.map((element) => parseRange(element));
    const start = Math.min(...parsedRanges.map((r) => r.start));
    const stop = Math.max(...parsedRanges.map((r) => r.stop));
    return { start, stop, str: `${start}-${stop}` };
}
/**
 * Combine any number of glyph (SDF) PBFs.
 * Returns a re-encoded PBF with the combined font faces, composited using array order
 * to determine glyph priority.
 * @param buffers An array of SDF PBFs.
 * @param fontstackName Optional font stack name.
 * @returns Combined glyph PBF.
 */
export function combine(buffers, fontstackName) {
    if (buffers.length === 0)
        return;
    if (buffers.length === 1)
        return buffers[0];
    const combinedGlyphs = [];
    const coverage = new Set();
    const names = [];
    const rangeStrings = [];
    for (const buffer of buffers) {
        const { stacks: [currentFontstack], } = decode(buffer);
        const { glyphs: currentGlyphs = [], range: fsRange, name: currentFontstackName, } = currentFontstack || {};
        if (fsRange)
            rangeStrings.push(fsRange);
        if (currentFontstackName)
            names.push(currentFontstackName);
        for (const glyph of currentGlyphs) {
            if (!coverage.has(glyph.id)) {
                combinedGlyphs.push(glyph);
                coverage.add(glyph.id);
            }
        }
    }
    combinedGlyphs.sort((a, b) => a.id - b.id);
    const range = rangeStrings.length > 0
        ? combineRanges(rangeStrings)
        : range256(Math.min(...coverage));
    const { str: rangeStr } = range;
    const resultFontstack = create(fontstackSchema, {
        name: fontstackName || names.join(", "),
        glyphs: combinedGlyphs,
        range: rangeStr || "",
    });
    return encode(create(glyphsSchema, { stacks: [resultFontstack] }));
}
export { debug };
export * from "./gen/glyphs_pb.js";
//# sourceMappingURL=index.js.map
/* Compute non-overlapping top positions in trim-relative percentages. */
(function (root) {
  'use strict';
  root.fitCoverBlocks = function (blocks, bottom, gap, top) {
    let cursor = Number.isFinite(top) ? top : 8;
    const latest = new Array(blocks.length);
    let availableBottom = bottom;
    // Reserve room for the following blocks before applying preferred positions.
    for (let index = blocks.length - 1; index >= 0; index--) {
      const block = blocks[index];
      if (!Number.isFinite(block.height) || block.height <= 0) return null;
      latest[index] = Math.floor(Math.min(block.maximum, availableBottom - block.height));
      availableBottom = latest[index] - gap;
    }
    const positions = [];
    for (const [index, block] of blocks.entries()) {
      const earliest = Math.ceil(Math.max(cursor, block.minimum));
      if (earliest > latest[index]) return null;
      const position = Math.max(earliest, Math.min(Math.ceil(block.preferred), latest[index]));
      positions.push(position);
      cursor = position + block.height + gap;
    }
    return positions;
  };
}(typeof globalThis !== 'undefined' ? globalThis : window));

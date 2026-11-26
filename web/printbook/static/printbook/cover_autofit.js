/* Compute non-overlapping top positions in trim-relative percentages. */
(function (root) {
  'use strict';
  root.fitCoverBlocks = function (blocks, bottom, gap, top) {
    let cursor = Number.isFinite(top) ? top : 8;
    const positions = [];
    for (const block of blocks) {
      if (!Number.isFinite(block.height) || block.height <= 0) return null;
      const top = Math.ceil(Math.max(cursor, block.minimum, block.preferred));
      if (top > block.maximum || top + block.height > bottom) return null;
      positions.push(top);
      cursor = top + block.height + gap;
    }
    return positions;
  };
}(typeof globalThis !== 'undefined' ? globalThis : window));

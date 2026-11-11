/** @deprecated Archived v2 compatibility layout. Unified preview/Excel/PDF use
 * kernel/document-layout.js. Preserve this adapter only for archived integrations
 * and explicit source replay; it is not the default unified export pipeline. */
// Optional DOCX export shares the active late Excel SOP grid.
export {makeModernWordSop as makeModernSopDocx,buildModernGridWordParts} from './grid-word-sop.js';

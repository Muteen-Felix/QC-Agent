// lodash-lite 1.0.0 (third party)
module.exports = { chunk: (a, n) => a.length ? [a.slice(0, n), ...module.exports.chunk(a.slice(n), n)] : [] };

# SQLite schema visualizations

Generated on 2026-10-28 with [sqleton](https://github.com/inukshuk/sqleton)
4.0.0 from the local Helsinki capital-area databases, opened read-only:

| Database | Schema version | Visualization | Graphviz source |
| --- | --- | --- | --- |
| `network.sqlite` | 3 | [SVG](network-schema.svg) | [DOT](network-schema.dot) |
| `numeric-routing-v2.sqlite` | 2 | [SVG](numeric-routing-schema.svg) | [DOT](numeric-routing-schema.dot) |

Open the SVG files in VS Code's image preview or a browser. An asterisk marks a
primary-key column. Arrows show declared foreign keys, with endpoint column
labels. Indexes appear beneath each table's columns. Composite foreign keys
and logical relationships should be checked against the SQL definitions.

The diagrams include SQLite's R-tree implementation tables, whose names end in
`_node`, `_parent`, and `_rowid`. R-tree number-based joins are not declared SQL
foreign keys and therefore have no arrows. The network diagram also includes
the graph chunk tables present in the exported database.

## Regeneration

Install Node.js, sqleton, and Graphviz. From the repository root, generate SVGs:

```sh
npm install -g sqleton@4.0.0
sqleton -L dot -e -o docs/reference/network-schema.svg \
  data/paths/prepared/helsinki-capital-area/network.sqlite
sqleton -L dot -e -o docs/reference/numeric-routing-schema.svg \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v2.sqlite
```

Replace `.svg` with `.dot` in the output paths to regenerate editable Graphviz
sources. Graphviz is a separate system dependency; on macOS install it with
`brew install graphviz`.

The checked-in diagrams used sqleton's Node API with Node's built-in SQLite
reader and the `@viz-js/viz` 3.31.0 WebAssembly Graphviz renderer (`dot` engine),
so generation did not require a system Graphviz installation. Layout and font
metrics may differ when regenerating with the native CLI.

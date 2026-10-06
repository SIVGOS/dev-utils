-- Give tables explicit column widths so Word/LibreOffice keep them inside the page.
-- Pandoc leaves widths unset (0) for pipe tables whose lines fit within --columns,
-- which makes the docx writer emit an empty grid and the table can run off the page.
-- Widths are proportional to each column's longest cell (clamped), summing to 1.

local function text_len(blocks)
  return #pandoc.utils.stringify(pandoc.Div(blocks))
end

function Table(tbl)
  if tbl.widths then
    local set = false
    for _, w in ipairs(tbl.widths) do if w and w > 0 then set = true end end
    if set then return nil end
  end
  local n = #tbl.aligns
  if n == 0 then return nil end
  local max = {}
  for i = 1, n do max[i] = 0 end
  local function scan(row)
    for i = 1, n do
      if row[i] then max[i] = math.max(max[i], text_len(row[i])) end
    end
  end
  scan(tbl.headers)
  for _, row in ipairs(tbl.rows) do scan(row) end
  local total = 0
  for i = 1, n do
    max[i] = math.min(math.max(max[i], 6), 60)
    total = total + max[i]
  end
  local out = {}
  for i = 1, n do out[i] = max[i] / total end
  tbl.widths = out
  return tbl
end

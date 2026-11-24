-- Translate only the inline HTML emitted by our Markdown renderer.
-- Keep the parsed inline nodes (including Math), never reparse arbitrary HTML.
local styles = {
  strong = pandoc.Strong,
  em = pandoc.Emph,
  u = pandoc.Underline,
  s = pandoc.Strikeout,
}

function Inlines(inlines)
  local stack = {{content = pandoc.List()}}

  local function append(inline)
    stack[#stack].content:insert(inline)
  end

  local function close_style()
    local frame = table.remove(stack)
    append(styles[frame.tag](frame.content))
  end

  for _, inline in ipairs(inlines) do
    if inline.t == 'RawInline' and inline.format == 'html' then
      local tag = inline.text:match('^%s*<([a-z]+)>%s*$')
      local closing = inline.text:match('^%s*</([a-z]+)>%s*$')
      if tag and styles[tag] then
        table.insert(stack, {tag = tag, content = pandoc.List()})
      elseif closing and #stack > 1 and stack[#stack].tag == closing then
        close_style()
      elseif inline.text:match('^%s*<br%s*/?>%s*$') then
        append(pandoc.LineBreak())
      end
      -- Drop comments and every other raw HTML tag. Their parsed text content
      -- remains in the inline list, and no unknown markup can be executed.
    else
      append(inline)
    end
  end

  -- Preserve all text even if a future renderer emits unclosed markup.
  while #stack > 1 do
    local frame = table.remove(stack)
    stack[#stack].content:extend(frame.content)
  end
  return stack[1].content
end

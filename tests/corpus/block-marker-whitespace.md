Introducing the four markers whose indent used to be counted in characters
rather than measured in columns.

One tab is four columns, so the line below opens no heading and the line under
it is this paragraph's lazy continuation rather than a code block.
	# Not a heading
    Prose that used to vanish beneath it.

An ideographic space is not indentation either, and it is the zh-TW paragraph
indent, so it arrives in ordinary translated prose.
　# Not a heading either
    Prose that used to vanish beneath this one.

The run after the hashes is spaces or tabs as well.
#　Not a heading, an ordinary paragraph
    And its own lazy continuation.

A thematic break ends in spaces or tabs, so a full-width space after one leaves
an ordinary paragraph behind.
***　
    Prose below a break that is not a break.

A tab before a break is four columns, one past the three a break may carry.
	***
    Prose below a tab-indented break.

A setext underline ends the same way.
===　
    Prose below an underline that is not an underline.

A link reference definition is indented with spaces.

　[not-a-ref]: /url
    Prose below a definition that is not one.

And the half that has to keep working.

# A real heading

   ### A real heading three columns in

#	A real heading whose hashes are followed by a tab

***

A real setext heading
=====================

[real]: /url "with a title"

-    An item whose own link definition is indented past three columns.

     [kept]: /url

Prose after all of it.

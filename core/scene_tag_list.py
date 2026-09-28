"""
core/scene_tag_list.py

Built-in phrase list for core/scene_tags.py: bracketed filename tags
that the pattern rules there can't recognise on their own. One phrase
per line, already normalized (lower-case, spaces not underscores).
Anything the patterns already classify correctly ("<name>-Empire",
"<name>-DCP", "Minutemen-<name>", "<N> covers", "<N>px", "c2c", ...)
is deliberately NOT listed here.

Seeded 2026-09-28 from the maintainer's own ZenCBR training list
(cleaned: duplicates, file-extension leftovers and 1-2 letter fragments
removed). A user's own classifications are stored separately and win
over this list.
"""

from __future__ import annotations


_SCAN = """
100% crcrew
2 fiche
abc
abpc
abraxsis
agarthy & friends
alexander tuesday
algol-starhome
alusias+artnet
anemoteph
antscan
apbc
aquila
aquila e corpz
aquila e mal32
aquila newecos 1a1
aquila omega
aquilalorelei trwbd
artnet
asp-pmack
asp-pmack-gambit
asp-pmack-teachbug
asp-pmack-tomander
bad kitty
bad kitty & chums
beast
beast's library
beepboop
bhcomics
bhcomics-pmack
bizzy beaver & --
blackbeard
brainiac.data9724
brianzc
brigus
brigus & bluejeff1954
brigus & gambit
brigus & teachbug
brigus&tomander
brigus-bhcomics-pmack
brigus-gambit
brigus-pmack
brigus-woodman
bunny
by eva
by rombo
by roy
by.roy
chrisb
chums
clasher
clickwheel
colorazione 1.1 by roy
colorazione 1.2 by roy
colorazione by roy
colorazione originale
colori
comichost-emcee
comicscastle
conan the librarian
corrected
corretto
cps-archangel-oroboros
crg
crgfunding
crx
ctc
cut-book
d&m
dakota scanning
dangerking
darel
darthscanner
darwination
darwination-gambit
dave-spkmn
delboy
dell4c & gambit
dell4c & tomander
dell4c and teachbug
dell4c-snard
dim
dlhii
dragonz
dragonz-choky-onairam-tanim
dreamer
drunkduck.com
drvink
edit
edit.roy
ego
elf-teachbug
even steven
evil-woodman
fatnerd
fbscan
ffly
fiche
fix
found
found - rombo
freddyfly
fullbeard
fylgja
g-seti
gambit
gambit & duckdodgers
gambit-darwin
gambit-darwination
gambit-pmack
gil69 ewscan
giuseppe031 & mystere & dinofelix
glorith
glorith-hd
griffin
grundy
gt & minutemen-thekid
gt edition & minutemen-thekid
happy 2nd scanniversary kman!
hp-woodman
humperdido
hyperborean
imbie
imbie-resin
inc
janus edit
jaoa-arc-caminante
jetstone
jimpy
jnx
jodyanimator
jodyanimator-chums
jojo
jtr-getcomics
jvj-rangerhouse-dmiles
jvj-rangerhouse-kracalactaka
k+comicwanderer
kingspyder
kman
kman and the grump
kman+jediknut
kman+resin
kman00001
kracalactaka
kracalactaka-dmiles
kracalactaka-editor
l169
l169-dbt
lavalamp
leduch
legion-cps-archangel
legoman
lindalee
lucaz
lucky
luminaro
mad doctor doom
madness
magiccarpetburn
mal32-gambit
mal32-secret santa
malibu guy
marih - miao films - fix
max zeus
maxx
mediozo
megan
meganubis
mentok
mickrc-sz
microfiche
misterno.e.capitan.ultra
mrlebaron
mrwoodman
myrbie
narfstar
narsftar
neverglades
newcomic.info
nightfist
njzombie
nodge
nom
ns2011
ns2012
obi
omoikitte
ont and pmack
ontology
ow-ont
ow-ontology-tomander
p.u.l.p
patterns
pd64 hqs
petethepipster
phillywilly
phillywilly-sosich
pico57
pmack
pmack & montagnard93-asp
pmack-brigus
pmack-darwination
pmack-gambit
pmack-gambit-nodge
pmack-jimmy hooten
pmack-teachbug
pmack-tomander
popbot - artnet
pudgy
pullboxonline.com
pyramid
pyrate
rangerhouse-yoc
reedit by roy
reiu
remix
ris noc2c
rolster
rolster-loftypilot
rolster-yocitrus
romanus & sosich
rombo
roy
rstones
rstones&esad
samox da pdf
scalliwags
scandog
scandog & artnet
scandog+artnet
scansl
series
shepherd+thebastard
sigasahab
skinny mulligan
sohex
soothsayr-dh
soothsayr-yoc
sosich
sosich & v2
sosich - agarthy
sosich-nemo-cst
sparkman
sparkman hqs
srca1941
stillontheedge
suineg
supermack
superscan
talon - super real - 2010
talon - super real graphics - 2010
talon - super real graphics - 2012
talon - super real graphics - 2014
tancombs
tdfk
teach-woodman
teachbug
teachbug & brigus
teachbug-gambit
team mal
team mal and friends
team mal32
teddyk
the sloppy bear-quantumdeep
the-hung
thebastard
titansfan
titansfan+editor
titansfan-comicscastle
titansfan-crx
titansfan-daveh
titansfan-dmiles
titansfan-editor
titansfan-kracalactaka
tomjoad
torquemada
trango
tsc-tyler
twobyfour
twobyfour-gambit
unknown - rebuild by jannella
vgm
wezz
wildbluezero
wilddog
wizard
wolfscan
woodman-madness
wowio
xra9
yz1
zeg
zen reaper
zero-rips
zifer-86
ziplizard
zombietoaster
zone-minutemen
zukiki
"""

_FORMAT = """
"""

_NOTE = """
+cvr art
alternate cover
black and white exclusive
close-up
colored reprint
covers
covers+extras
flipbook
large
last
limited edition extras
msg 6 pgs
needs help
partial rpt
random pages
read nfo
regular cover
remix with gn01
text
universe cover
with alt cover
with page fills
"""

_HINT = """
1956.atlas
1970 archie comic publications
215 ink
aircel
american mythology
american mythology productions
betty dobson
black and white
boichi
bonelli 2012-08
bonelli 2017
charlie adlard
comic art
eric schumacher
febbraio 2021
grant gardner
joe kubert
magnetic press
marvel authentix
papercutz
pied piper
short stories
tim daniel & mehdi cheggour
various short
"""


def _parse(block: str, category: str) -> dict[str, str]:
    return {line.strip(): category for line in block.splitlines() if line.strip()}


SCENE_TAGS: dict[str, str] = {
    **_parse(_SCAN, "scan"),
    **_parse(_FORMAT, "format"),
    **_parse(_NOTE, "note"),
    **_parse(_HINT, "hint"),
}

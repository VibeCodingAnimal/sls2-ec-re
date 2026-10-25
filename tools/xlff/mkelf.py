import struct
IMG="/tmp/ecre/imgA.bin"; OUT="/tmp/ecre/ec_fw.elf"
BASE=0x1007F994; ENTRY=0x10080000
img=open(IMG,"rb").read(); L=len(img)
def uleb(v):
    o=b""
    while True:
        b=v&0x7f; v>>=7
        o+=bytes([b|0x80]) if v else bytes([b])
        if not v: return o
body  = uleb(0x05)+uleb(0x0D)+uleb(0x0C)+uleb(0x4D)+uleb(0x07)+uleb(0x02)+uleb(0x08)+uleb(0x02)
attr = b'A'+uleb(5)+uleb(len(body)+len(uleb(1)+uleb(len(body))+body))  # placeholder
inner = uleb(0x01)+uleb(len(body))+body
attr  = b'A'+uleb(5)+uleb(len(inner))+inner

shstrtab=b"\x00.text\x00.ARM.attributes\x00.shstrtab\x00"
N_T, N_A, N_S = 1, 7, 24
ehsize,phentsize,shentsize=52,32,40
phoff=ehsize; dataoff=phoff+phentsize
shstr_off=dataoff+L
attr_off=(shstr_off+3)&~3
shstrtab_off=attr_off+len(attr)
shoff=(shstrtab_off+len(shstrtab)+3)&~3

out=bytearray()
out+=b"\x7fELF"+bytes([1,1,1,0])+bytes(8)
out+=struct.pack("<HHIIIIIHHHHHH",2,40,1,ENTRY,phoff,shoff,0,ehsize,phentsize,1,shentsize,4,3)
out+=struct.pack("<IIIIIIII",1,0,BASE,BASE,L,L,0x7,0x1000)
out+=b"\x00"*(dataoff-len(out)); out+=img
out+=b"\x00"*(attr_off-len(out)); out+=attr
out+=shstrtab; out+=b"\x00"*(shoff-len(out))
def sh(name,typ,flags,addr,off,size,link,info,align,ent):
    return struct.pack("<IIIIIIIIII",name,typ,flags,addr,off,size,link,info,align,ent)
out+=sh(0,0,0,0,0,0,0,0,0,0)                       # NULL
out+=sh(N_T,1,0x6,BASE,dataoff,L,0,0,4,0)          # .text PROGBITS AX
out+=sh(N_A,0x70000003,0,0,attr_off,len(attr),0,0,1,0)  # .ARM.attributes
out+=sh(N_S,3,0,0,shstrtab_off,len(shstrtab),0,0,1,0)    # .shstrtab
open(OUT,"wb").write(out)
print("wrote %d bytes" % len(out))

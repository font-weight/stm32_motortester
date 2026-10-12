from pathlib import Path
import hashlib, json

E = Path(__file__).parent
U = E/'upstream'
A = Path('/tmp/host-license-gap-audit/upstream')
sha = lambda b: hashlib.sha256(b).hexdigest()
proposal = Path('/tmp/host-license-gap-audit/NOTICE-SUPPLEMENT-PROPOSAL.md').read_bytes()
assert sha(proposal) == '462575dcd73418667cf1b62b63906a56166b57a243e82b5ddfc76327f442197f'
(E/'NOTICE-SUPPLEMENT-PROPOSAL.reviewed.md').write_bytes(proposal)
tags = {}
for prefix, tag_sha, commit in [
    ('pyside', '6aefae25747a8d860d201f3ab2b2e199b9e8e006', 'f50facb83fc3dc5f3240c854668c7a6dd93871b2'),
    ('qtbase', 'fcef5b2c755492753eb8f7424dd2bc7337832068', '025bdad181de241e81bf853c8a2d7bf3d19261a9')]:
    ref = json.loads((U/(prefix+'_ref.json')).read_text())
    tag = json.loads((U/(prefix+'_tag.json')).read_text())
    assert ref['ref'] == 'refs/tags/v6.12.0' and ref['object']['sha'] == tag_sha and ref['object']['type'] == 'tag'
    assert tag['sha'] == tag_sha and tag['object']['sha'] == commit and tag['object']['type'] == 'commit'
    assert tag['verification']['verified'] is False and tag['verification']['reason'] == 'unsigned'
    tags[prefix] = {'tag_object': tag_sha, 'commit': commit, 'signature_verified': False}
entries = {prefix: {x['name']: x for x in json.loads((U/(prefix+'_listing.json')).read_text())} for prefix in tags}
records = []
for name, key, count, blob, digest in [
    ('GPL-2.0-only.txt', 'GPL2', 18092, 'd159169d1050894d3ea3b98e1c965c4058208fe1', '8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643'),
    ('GPL-3.0-only.txt', 'GPL3', 35147, '94a9ed024d3859793618152ea559a168bbcbb5e2', '8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903'),
    ('LGPL-3.0-only.txt', 'LGPL3', 7651, '65c5ca88a67c30becee01c5a8816d964b03862f9', 'da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768')]:
    data = (U/(key+'.txt')).read_bytes()
    assert len(data) == count and sha(data) == digest
    assert hashlib.sha1(b'blob '+str(count).encode()+b'\0'+data).hexdigest() == blob
    assert data == (A/name).read_bytes()
    record = json.loads((A/(name+'.json')).read_text())
    assert record['content'].encode() == data
    for prefix in tags:
        assert entries[prefix][name]['sha'] == blob and entries[prefix][name]['size'] == count
    records.append({'name':name, 'bytes':count, 'git_blob':blob, 'sha256':digest, 'author_input_matches':True, 'pyside_qtbase_listing_matches':True})
result = {'status':'PASS input provenance only', 'tags':tags, 'notices':records, 'scope':'Exact immutable upstream license-text bytes; no corresponding-source or legal clearance claim', 'candidate_tested':False}
(E/'UPSTREAM-VERIFIED.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))

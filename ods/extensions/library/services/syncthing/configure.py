"""Conservative initial settings; subsequent owner changes are preserved."""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path('/var/syncthing')
config = root / 'config/config.xml'
tree = ET.parse(config)
document = tree.getroot()
options = document.find('options')
if options is None or document.find('gui/password') is None:
    raise RuntimeError('Generated Syncthing configuration is incomplete')
for folder in list(document.findall('folder')):
    document.remove(folder)
values = {
    'globalAnnounceEnabled': 'false', 'localAnnounceEnabled': 'false',
    'relaysEnabled': 'false', 'natEnabled': 'false', 'startBrowser': 'false',
    'urAccepted': '-1', 'autoUpgradeIntervalH': '0', 'crashReportingEnabled': 'false',
}
for key, value in values.items():
    element = options.find(key)
    if element is None:
        element = ET.SubElement(options, key)
    element.text = value
for element in list(options.findall('listenAddress')):
    options.remove(element)
ET.SubElement(options, 'listenAddress').text = 'tcp://0.0.0.0:22000'
temporary = config.with_suffix('.ods-tmp')
tree.write(temporary, encoding='utf-8', xml_declaration=True)
os.chmod(temporary, 0o600)
os.replace(temporary, config)
(root / 'files').mkdir(exist_ok=True)
(root / '.ods-initialized').write_text('1\n', encoding='ascii')

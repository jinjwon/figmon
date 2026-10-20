"""앞에 있는 앱 읽기(lsappinfo) 회귀 테스트: macOS 27에서 출력 형식이 바뀌어 앱 이름이 항상 비었고, 유튜브가 딴짓으로 안 잡혔음."""
import os
import tempfile
import unittest

os.environ["FIGMON_HOME"] = tempfile.mkdtemp()
import server  # noqa: E402

OLD = '''"LSDisplayName"="Brave Browser"
"LSBundlePath"="/Applications/Brave Browser.app"
'''
NEW = '''"Brave Browser" ASN:0x0-0x3e03e: (in front) 
    bundleID=[ NULL ] 
    bundle path="/Applications/Brave Browser.app"
    executable path=[ NULL ] 
 !cgsConnection !signalled type=[ NULL ]  flavor=[ NULL ]  Version=[ NULL ]  Arch=!!none 
'''
NEW_LIST = ''' 1) "Figma" ASN:0x0-0x1001: 
    bundle path="/Applications/Figma.app"
'''


class FrontAppTests(unittest.TestCase):
    def test_old_format(self):
        self.assertEqual(server.parse_lsappinfo(OLD), ("Brave Browser", "/Applications/Brave Browser.app"))

    def test_macos27_format(self):
        self.assertEqual(server.parse_lsappinfo(NEW), ("Brave Browser", "/Applications/Brave Browser.app"))
        self.assertEqual(server.parse_lsappinfo(NEW_LIST), ("Figma", "/Applications/Figma.app"))

    def test_unreadable_is_empty(self):
        self.assertEqual(server.parse_lsappinfo(""), ("", ""))
        self.assertEqual(server.parse_lsappinfo('[ NULL ]  ASN:0x0-0x1: \n    bundle path=[ NULL ] \n'), ("", ""))

    def test_youtube_in_brave_is_distraction(self):
        name, _ = server.parse_lsappinfo(NEW)
        self.assertEqual(server.classify(name, "https://www.youtube.com/watch?v=x", server.DEFAULT_CONFIG)[0], "distract")


if __name__ == "__main__":
    unittest.main()

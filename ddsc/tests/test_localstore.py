from unittest import TestCase
import os
import shutil
import tarfile
from ddsc.localstore import LocalFile, LocalFolder, LocalProject


class TestProjectFolderFile(TestCase):
    def test_file_str(self):
        f = LocalFile('setup.py')
        self.assertEquals('file:setup.py', str(f))

    def test_empty_folder_str(self):
        f = LocalFolder('stuff')
        self.assertEquals('folder:stuff []', str(f))

    def test_folder_one_child_str(self):
        folder = LocalFolder('stuff')
        folder.add_child(LocalFile('setup.py'))
        self.assertEquals('folder:stuff [file:setup.py]', str(folder))

    def test_folder_two_children_str(self):
        folder = LocalFolder('stuff')
        folder.add_child(LocalFile('setup.py'))
        folder.add_child(LocalFile('requirements.txt'))
        self.assertEquals('folder:stuff [file:setup.py, file:requirements.txt]', str(folder))

    def test_nested_folder_str(self):
        grand = LocalFolder('grand')
        parent = LocalFolder('parent')
        parent.add_child(LocalFile('setup.py'))
        parent.add_child(LocalFile('requirements.txt'))
        otherparent = LocalFolder('otherparent')
        grand.add_child(parent)
        grand.add_child(otherparent)
        self.assertEquals(('folder:grand ['
                           'folder:parent [file:setup.py, file:requirements.txt], '
                           'folder:otherparent []]'), str(grand))


class TestProjectContent(TestCase):
    """
    These tests exercise code that interacts with the file system.
    We extract ddsc/tests/testfolder.tar to temp and tests are specific to that tar file.
    """
    @classmethod
    def setUpClass(cls):
        test_folder = tarfile.TarFile('ddsc/tests/testfolder.tar')
        test_folder.extractall('/tmp')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree('/tmp/DukeDsClientTestFolder')

    def test_folder_dot_name(self):
        content = LocalFolder('.')
        self.assertEquals('DukeDSClient', content.name)

    def test_folder_name_removes_slash(self):
        content = LocalFolder('/tmp/DukeDsClientTestFolder/')
        self.assertEquals('DukeDsClientTestFolder', content.name)
        self.assertEquals('/tmp/DukeDsClientTestFolder', content.path)

    def test_folder_name_no_slash(self):
        content = LocalFolder('/tmp/DukeDsClientTestFolder')
        self.assertEquals('DukeDsClientTestFolder', content.name)
        self.assertEquals('/tmp/DukeDsClientTestFolder', content.path)

    def test_folder_up_and_back(self):
        content = LocalFolder('../DukeDSClient')
        self.assertEquals('DukeDSClient', content.name)

    def test_empty_str(self):
        content = LocalProject(False)
        self.assertEquals('project: []', str(content))

    def test_top_level_file_str(self):
        content = LocalProject(False)
        content.add_path('/tmp/DukeDsClientTestFolder/note.txt')
        self.assertEquals('project: [file:note.txt]', str(content))

    def test_empty_folder_str(self):
        content = LocalProject(False)
        content.add_path('/tmp/DukeDsClientTestFolder/emptyfolder')
        self.assertEquals('project: [folder:emptyfolder []]', str(content))

    def test_empty_folder_and_file_str(self):
        content = LocalProject(False)
        content.add_path('/tmp/DukeDsClientTestFolder/emptyfolder')
        content.add_path('/tmp/DukeDsClientTestFolder/note.txt')
        self.assertEquals('project: [folder:emptyfolder [], file:note.txt]', str(content))

    def test_one_folder_str(self):
        content = LocalProject(False)
        content.add_path('/tmp/DukeDsClientTestFolder/scripts')
        self.assertEquals('project: [folder:scripts [file:makemoney.sh]]', str(content))

    def test_nested_folder_str(self):
        content = LocalProject(False)
        content.add_path('/tmp/DukeDsClientTestFolder/results')
        self.assertEquals(('project: [folder:results ['
                           'file:result1929.txt, '
                           'file:result2929.txt, '
                           'folder:subresults [file:result1002.txt, file:result13.txt, file:result15.txt], '
                           'folder:subresults2 []'
                           ']]'), str(content))

    def test_big_folder_str(self):
        content = LocalProject(False)
        content.add_path('/tmp/DukeDsClientTestFolder')
        self.assertEquals(('project: [folder:DukeDsClientTestFolder ['
                           'file:note.txt, '
                           'folder:emptyfolder [], '
                           'folder:results ['
                           'file:result1929.txt, file:result2929.txt, folder:subresults '
                           '[file:result1002.txt, file:result13.txt, file:result15.txt], '
                           'folder:subresults2 []'
                           '], '
                           'folder:scripts ['
                           'file:makemoney.sh'
                           ']'
                           ']]'), str(content))
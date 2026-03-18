"""Tests for AI Test Runner CLI."""

import os
import pytest
import subprocess
from pathlib import Path as RealPath
from unittest.mock import patch, MagicMock
from ai_test_runner.cli import main, AITestRunner


class TestAITestRunner:
    """Test the AITestRunner class."""

    @patch('ai_test_runner.cli.subprocess.run')
    @patch('ai_test_runner.cli.shutil.copytree')
    @patch('ai_test_runner.cli.shutil.copy2')
    @patch('ai_test_runner.cli.Path')
    def test_find_compilable_tests(self, mock_path, mock_copy2, mock_copytree, mock_subprocess):
        """Test finding compilable tests."""
        # Mock Path to avoid directory creation issues
        mock_path_instance = MagicMock()
        mock_path.return_value = mock_path_instance

        runner = AITestRunner(repo_path='/fake/path')

        # Mock the verification directory methods
        runner.verification_dir = MagicMock()
        runner.verification_dir.exists.return_value = True
        report1 = MagicMock()
        report1.name = 'test1_compiles_yes.txt'
        report1.relative_to.return_value = RealPath('src/mod/test1_compiles_yes.txt')

        report2 = MagicMock()
        report2.name = 'test2_compiles_yes.txt'
        report2.relative_to.return_value = RealPath('src/mod/test2_compiles_yes.txt')

        runner.verification_dir.rglob.return_value = [report1, report2]

        # Mock the tests directory and test files
        runner.tests_dir = MagicMock()

        mid = MagicMock()
        runner.tests_dir.__truediv__.return_value = mid

        def mid_div(filename):
            mock_file = MagicMock()
            mock_file.exists.return_value = True
            mock_file.stem = str(filename).replace('.cpp', '').replace('.c', '')
            mock_file.name = str(filename)
            return mock_file
        mid.__truediv__.side_effect = mid_div

        tests = runner.find_compilable_tests()

        assert len(tests) == 2
        # Tests should now be Path objects, not strings
        assert tests[0].stem in ['test1', 'test2']
        assert tests[1].stem in ['test1', 'test2']

    @patch('ai_test_runner.cli.subprocess.run')
    def test_build_tests_success(self, mock_subprocess):
        """Test successful build."""
        mock_subprocess.return_value = MagicMock(returncode=0, stdout='Build successful', stderr='')

        runner = AITestRunner(repo_path='/fake/path')
        result = runner.build_tests()

        assert result is True
        mock_subprocess.assert_called()

    @patch('ai_test_runner.cli.subprocess.run')
    def test_build_tests_failure(self, mock_subprocess):
        """Test build failure."""
        mock_subprocess.side_effect = subprocess.CalledProcessError(1, 'cmake', stderr='Build failed')

        runner = AITestRunner(repo_path='/fake/path')
        result = runner.build_tests()

        assert result is False

    @patch('ai_test_runner.cli.os.access')
    @patch('ai_test_runner.cli.subprocess.run')
    def test_run_tests_success(self, mock_subprocess, mock_access):
        """Test successful test execution."""
        mock_subprocess.return_value = MagicMock(returncode=0, stdout='All tests passed', stderr='')
        mock_access.return_value = True

        runner = AITestRunner(repo_path='/fake/path')
        # Mock some test executables
        runner.output_dir = MagicMock()
        mock_exe = MagicMock()
        mock_exe.is_file.return_value = True
        mock_exe.name = 'test_main.exe'
        mock_exe.suffix = '.exe'
        runner.output_dir.glob.return_value = [mock_exe]

        results = runner.run_tests()

        assert isinstance(results, list)
        # Should have one result for the successful test
        assert len(results) == 1
        assert results[0]['success']

    @patch('ai_test_runner.cli.os.access')
    @patch('ai_test_runner.cli.subprocess.run')
    def test_run_tests_failure(self, mock_subprocess, mock_access):
        """Test test execution with failures."""
        mock_subprocess.return_value = MagicMock(returncode=1, stdout='', stderr='Test failed')
        mock_access.return_value = True

        runner = AITestRunner(repo_path='/fake/path')
        # Mock some test executables
        runner.output_dir = MagicMock()
        mock_exe = MagicMock()
        mock_exe.is_file.return_value = True
        mock_exe.name = 'test_main.exe'
        mock_exe.suffix = '.exe'
        runner.output_dir.glob.return_value = [mock_exe]

        results = runner.run_tests()

        assert isinstance(results, list)
        # Should have one result for the failed test
        assert len(results) == 1
        assert not results[0]['success']


class TestCLI:
    """Test the CLI interface."""

    @patch('ai_test_runner.cli.AITestRunner')
    def test_main_success(self, mock_runner_class):
        """Test successful main execution."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        mock_runner.run.return_value = True

        with patch('sys.argv', ['ai-test-runner']):
            with pytest.raises(SystemExit):
                main()

    @patch('ai_test_runner.cli.AITestRunner')
    def test_main_no_tests_found(self, mock_runner_class):
        """Test when no compilable tests are found."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        mock_runner.run.return_value = False

        with patch('sys.argv', ['ai-test-runner']):
            with pytest.raises(SystemExit):
                main()

    def test_version(self):
        """Test version display."""
        with patch('sys.argv', ['ai-test-runner', '--version']):
            with pytest.raises(SystemExit):
                main()

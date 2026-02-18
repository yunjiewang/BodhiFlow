"""
Flow definitions for BodhiFlow - Content to Wisdom Converter.

This module defines PocketFlow workflows for processing various types of content:
- Videos (YouTube and local files)
- Audio files
- Documents and text content

The flows are designed to be modular and configurable, supporting both
content acquisition (Phase 1) and content refinement (Phase 2).
"""

from pocketflow import Flow

from nodes import (
    AsyncRefinementCoordinatorNode,
    FlowCompletionNode,
    InputExpansionNode,
    ParallelAcquisitionCoordinatorNode,
    RefinementTaskCreatorNode,
    TempFileCleanupNode,
)


def create_bodhi_flow() -> Flow:
    """
    Create the complete BodhiFlow workflow (both phases).

    This creates a full workflow that:
    1. Expands user input into processable sources
    2. Acquires content (transcripts/audio processing)
    3. Creates refinement tasks
    4. Refines content using language models
    5. Cleans up temporary files
    6. Provides completion summary

    Returns:
        Flow: A PocketFlow instance ready to process content
    """

    # Create all nodes
    input_expansion = InputExpansionNode()
    parallel_acquisition = ParallelAcquisitionCoordinatorNode()
    refinement_task_creator = RefinementTaskCreatorNode()
    async_refinement = AsyncRefinementCoordinatorNode()
    temp_cleanup = TempFileCleanupNode()
    flow_completion = FlowCompletionNode()

    # Phase 1: Content Acquisition connections
    input_expansion - "start_parallel_acquisition" >> parallel_acquisition
    input_expansion - "phase_1_complete_no_input" >> refinement_task_creator

    parallel_acquisition - "phase_1_complete" >> refinement_task_creator

    # Phase 2: Content Refinement connections
    refinement_task_creator - "start_async_refinement" >> async_refinement
    refinement_task_creator - "phase_2_complete_no_tasks" >> temp_cleanup

    async_refinement - "phase_2_complete" >> temp_cleanup

    # Cleanup and completion
    temp_cleanup - "cleanup_complete" >> flow_completion

    # flow_completion - "flow_complete" >> None  # End of flow implicitly handled

    # Create flow starting with input expansion
    return Flow(start=input_expansion)


def create_phase_1_only_flow() -> Flow:
    """
    Create a flow that only runs Phase 1 (content acquisition).

    Returns:
        Flow: Phase 1 only PocketFlow
    """

    # Create Phase 1 nodes
    input_expansion = InputExpansionNode()
    parallel_acquisition = ParallelAcquisitionCoordinatorNode()
    temp_cleanup = TempFileCleanupNode()
    flow_completion = FlowCompletionNode()

    # Phase 1 connections
    input_expansion - "start_parallel_acquisition" >> parallel_acquisition
    input_expansion - "phase_1_complete_no_input" >> flow_completion

    parallel_acquisition - "phase_1_complete" >> temp_cleanup

    temp_cleanup - "cleanup_complete" >> flow_completion

    # flow_completion - "flow_complete" >> None

    return Flow(start=input_expansion)


def create_phase_2_only_flow() -> Flow:
    """
    Create a flow that only runs Phase 2 (content refinement).

    This flow discovers existing transcript files and processes them.

    Returns:
        Flow: Phase 2 only PocketFlow
    """

    # Create Phase 2 nodes
    refinement_task_creator = RefinementTaskCreatorNode()
    async_refinement = AsyncRefinementCoordinatorNode()
    flow_completion = FlowCompletionNode()

    # Phase 2 connections
    refinement_task_creator - "start_async_refinement" >> async_refinement
    refinement_task_creator - "phase_2_complete_no_tasks" >> flow_completion

    async_refinement - "phase_2_complete" >> flow_completion

    # flow_completion - "flow_complete" >> None

    return Flow(start=refinement_task_creator)


# Factory function to create appropriate flow based on phase flags
def create_flow_for_phases(run_phase_1: bool, run_phase_2: bool) -> Flow:
    """
    Factory function to create the appropriate flow based on which phases to run.

    Args:
        run_phase_1 (bool): Whether to run Phase 1 (content acquisition)
        run_phase_2 (bool): Whether to run Phase 2 (content refinement)

    Returns:
        Flow: Appropriate PocketFlow for the selected phases

    Raises:
        ValueError: If neither phase is selected
    """

    if run_phase_1 and run_phase_2:
        return create_bodhi_flow()
    elif run_phase_1 and not run_phase_2:
        return create_phase_1_only_flow()
    elif not run_phase_1 and run_phase_2:
        return create_phase_2_only_flow()
    else:
        raise ValueError(
            "At least one phase must be selected (run_phase_1 or run_phase_2)"
        )

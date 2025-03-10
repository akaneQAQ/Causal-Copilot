import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import pandas as pd
from openai import OpenAI
import os
from pydantic import BaseModel

def convert_adj_mat(mat):
    # In downstream analysis, we only keep direct edges and ignore all undirected edges
    mat = np.array(mat)
    mat = (mat == 1).astype(int)
    G = mat.T
    return G

def coarsen_continuous_variables(data, cont_confounders, bins=5):
    """
    Coarsen continuous variables into bins for CEM.
    
    :param data: The dataset.
    :param cont_confounders: List of continuous confounder column names.
    :param bins: Number of bins to create for each continuous variable.
    :return: Dataset with coarsened columns.
    """
    for col in cont_confounders:
        if col in data.columns:
            coarsened_col = f'coarsen_{col}'
            #data[coarsened_col] = pd.cut(data[col], bins=bins, labels=False)
            bin_edges = pd.cut(data[col], bins=bins)
            data[coarsened_col] = bin_edges.apply(lambda interval: f"{int(interval.left)}-{int(interval.right)}")
    return data

def plot_hte_dist(hte, fig_path):
    plt.figure(figsize=(8, 6))
    sns.histplot(hte['hte'], bins=30, color=sns.color_palette("muted")[0], kde=True, alpha=0.7)
    plt.axvline(hte['hte'].mean(), color='firebrick', linestyle='--', label='Mean HTE')
    plt.xlabel("Heterogeneous Treatment Effect (HTE)")
    plt.ylabel("Frequency")
    plt.title("Distribution of Heterogeneous Treatment Effects")
    # Save figure
    plt.savefig(fig_path)

def plot_cate_violin(global_state, hte, X_col, fig_path):
    data = global_state.user_data.processed_data.copy()
    cont_X_col = [var for var in X_col if global_state.statistics.data_type_column[var]=='Continuous']
    coarsen_data = coarsen_continuous_variables(data, cont_X_col)
    data = pd.concat([coarsen_data, hte], axis=1)
    num_groups = len(X_col)
    fig, axes = plt.subplots(1, num_groups, figsize=(10 * num_groups, 6), sharex=False)
    if num_groups == 1:
        axes = [axes]  # Ensure axes is always a list for consistency

    for ax, group_col in zip(axes, X_col):
        if group_col in cont_X_col:
            x=f'coarsen_{group_col}'
        else:
            x=group_col
        palette = sns.color_palette("Blues", n_colors=len(data[x].unique()))
        sns.violinplot(x=x, y='hte', data=data, ax=ax, 
                       inner="quartile", density_norm="width", 
                       hue=x, legend=False, palette=palette)
        # Customize the subplot
        ax.set_title(f"CATE Distribution by {group_col.capitalize()}")
        ax.set_xlabel(group_col.capitalize())
        ax.set_ylabel("CATE")
        ax.grid(True)
    # Adjust layout to prevent overlap
    plt.tight_layout()
    plt.savefig(fig_path)

def plot_cate_bars_by_group(cate_result, fig_path):
    n_groups = len(cate_result)
    n_rows = (n_groups + 2) // 3  # Calculate number of rows needed (3 plots per row)
    n_cols = min(3, n_groups)
    fig = plt.figure(figsize=(7*n_cols, 6*n_rows))
    for idx, group in enumerate(cate_result.keys()):
        plt.subplot(n_rows, n_cols, idx + 1)
        # Create bar plot for each group
        sns.barplot(x=cate_result[group].index, y=cate_result[group].values, color='skyblue', alpha=0.7)
        plt.title(f"CATE - {group}")
        plt.xlabel("Group Label")
        plt.ylabel("CATE")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    plt.tight_layout()
    plt.savefig(fig_path, bbox_inches='tight')
    plt.close()

def generate_density_plot(global_state, data, matched_data, treatment, confounders, title):
    """
    Generate a single density plot with subplots for different confounders.
    Each row corresponds to a confounder, with treated and control groups in separate subplots.
    """
    sns.set_style("darkgrid")  
    num_confounders = len(confounders)
    fig, axes = plt.subplots(nrows=num_confounders, ncols=2, figsize=(20, 6 * num_confounders))
    
    if num_confounders == 1:
        axes = [axes]  

    for i, confounder in enumerate(confounders):
        # Before Matching (left subplot)
        ax_treated = axes[i][0]
        sns.kdeplot(
            data[data[treatment] == 1][confounder], 
            label='Treated (Unmatched)', 
            color='#409fde', 
            fill=True, 
            alpha=0.3, 
            ax=ax_treated
        )
        sns.kdeplot(
            data[data[treatment] == 0][confounder], 
            label='Control (UnMatched)', 
            color='#f77f3e', 
            fill=True,  
            alpha=0.3,  
            ax=ax_treated
        )
        ax_treated.set_title(f'Before Matching: {confounder} ({title})')
        ax_treated.set_xlabel(confounder)
        ax_treated.set_ylabel('Density')
        ax_treated.legend()
        ax_treated.grid(True)

        # Control group (right subplot)
        ax_control = axes[i][1]
        sns.kdeplot(
            matched_data[matched_data[treatment] == 1][confounder], 
            label='Treated (matched)', 
            color='#409fde', 
            fill=True,  
            alpha=0.3,  
            ax=ax_control
        )
        sns.kdeplot(
            matched_data[matched_data[treatment] == 0][confounder], 
            label='Control (Matched)', 
            color='#f77f3e', 
            fill=True, 
            alpha=0.3,  
            ax=ax_control
        )
        ax_control.set_title(f'After Matching: {confounder} ({title})')
        ax_control.set_xlabel(confounder)
        ax_control.set_ylabel('Density')
        ax_control.legend()
        ax_control.grid(True)
    plt.tight_layout()

    # Save the density plot
    density_plot_filename = f'density_plot_{title.lower().replace(" ", "_")}.png'
    density_plot_path = os.path.join(global_state.user_data.output_graph_dir, density_plot_filename)
    plt.savefig(density_plot_path, bbox_inches='tight')
    plt.close()
    figs = [density_plot_path]
    return figs

def LLM_parse_query(args, format, prompt, message):
    client = OpenAI(organization=args.organization, project=args.project, api_key=args.apikey)
    if format:
        completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini-2024-07-18",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ],
        response_format=format,
        )
        parsed_response = completion.choices[0].message.parsed
    else: 
        completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini-2024-07-18",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ],
        )
        parsed_response = completion.choices[0].message.content
    return parsed_response

def LLM_select_confounders(treatment, key_node, args, data):
    class ConfounderList(BaseModel):
        confounders: list[str]
    prompt = f"""
I'm doing the Treatment Effect Estimation analysis, please identify possible confounders between the treatment and result variables.
Treatment: {treatment}
Result: {key_node}
The confounders must be among these variables: {data.columns}
Only return me with the variable name, do not include anything else.
"""
    parsed_response = LLM_parse_query(args, ConfounderList, 'You are an expert in Causal Discovery.', prompt)
    LLM_confounders = parsed_response.confounders
    return LLM_confounders 

def LLM_select_hte_var(args, treatment, key_node, message, data):
    class HTE_VarList(BaseModel):
            hte_variable: list[str]
    prompt = f"""
I'm doing the Treatment Effect Estimation analysis.
Firstly, identify whether there are heterogeneous variables in this query: {message}
If so, save these variables in the hte_variable list
Otherwise, please identify heterogeneous variables between the treatment and result variables, and save these variables in the hte_variable list
Please limit the number of heterogeneous variables within 5!
Treatment: {treatment}
Result: {key_node}
The heterogeneous variables must be among these variables: {data.columns}
Only return me with the variable name, do not include anything else.
"""
    parsed_response = LLM_parse_query(args, HTE_VarList, 'You are an expert in Causal Discovery.', prompt)
    hte_variables = parsed_response.hte_variable    
    return hte_variables

def check_binary(column):
    unique_values = column.unique()
    treat = max(column)
    control = min(column)
    return len(unique_values) == 2, treat, control
